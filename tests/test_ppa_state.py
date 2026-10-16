import contextlib
import importlib.util
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'ppa_state', Path(__file__).resolve().parents[1] / 'scripts/ppa-state.py')
STATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATE)


class CollectionRetryTest(unittest.TestCase):
    @contextlib.contextmanager
    def server(self, responses):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                response = responses[min(len(requests) - 1, len(responses) - 1)]
                if response is None:
                    self.close_connection = True
                    return
                if isinstance(response, int):
                    self.send_error(response)
                    return
                if isinstance(response, bytes):
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(response) + 20))
                    self.end_headers()
                    self.wfile.write(response)
                    self.close_connection = True
                    return
                response = dict(response)
                if response.get('next_collection_link', '').startswith('/'):
                    response['next_collection_link'] = (
                        f'http://127.0.0.1:{self.server.server_port}'
                        + response['next_collection_link'])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_empty_reply_is_retried(self):
        # Actions run 38887686129 failed with curl exit 52 at this call site.
        with self.server([None, {'entries': [1]}]) as (url, requests):
            self.assertEqual(STATE.collection(url), [1])
            self.assertEqual(len(requests), 2)

    def test_partial_response_is_discarded_before_retry(self):
        with self.server([b'{"entries": [999', {'entries': [1]}]) as (url, requests):
            with patch.object(STATE.time, 'sleep'):
                self.assertEqual(STATE.collection(url), [1])
            self.assertEqual(len(requests), 2)

    def test_http_service_unavailable_is_retried(self):
        with self.server([503, {'entries': [1]}]) as (url, requests):
            with patch.object(STATE.time, 'sleep'):
                self.assertEqual(STATE.collection(url), [1])
            self.assertEqual(len(requests), 2)

    def test_later_page_retries_without_duplicating_entries(self):
        responses = [{'entries': [1], 'next_collection_link': '/page2'},
                     None, {'entries': [2]}]
        with self.server(responses) as (url, requests):
            with patch.object(STATE.time, 'sleep'):
                self.assertEqual(STATE.collection(url), [1, 2])
            self.assertEqual(requests, ['/', '/page2', '/page2'])

    def test_persistent_empty_reply_fails_after_four_attempts(self):
        with self.server([None]) as (url, requests):
            with patch.object(STATE.time, 'sleep') as sleep:
                with self.assertRaises(subprocess.CalledProcessError) as error:
                    STATE.collection(url)
            self.assertEqual(error.exception.returncode, 52)
            self.assertEqual(len(requests), 4)
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2, 4])

    def test_invalid_json_is_not_treated_as_absent(self):
        with patch.object(STATE.subprocess, 'check_output', return_value='invalid') as curl:
            with self.assertRaises(json.JSONDecodeError):
                STATE.collection('https://fixture')
            self.assertEqual(curl.call_count, 1)

    def test_curl_has_connection_and_request_timeouts(self):
        with patch.object(STATE.subprocess, 'check_output', return_value='{"entries": []}') as curl:
            self.assertEqual(STATE.collection('https://fixture'), [])
            args = curl.call_args.args[0]
            self.assertEqual(args[args.index('--connect-timeout') + 1], '15')
            self.assertEqual(args[args.index('--max-time') + 1], '60')
            self.assertIn('Cache-Control: no-cache', args)


class PpaStateTest(unittest.TestCase):
    def decide(self, revisions):
        publications = []
        builds = {}
        for n, status, states in revisions:
            link = f'https://fixture/{n}'
            publications.append({'source_package_version': f'1.0-1~noble1~ppa{n}',
                                 'status': status, 'self_link': link,
                                 'distro_series_link': 'https://api.launchpad.net/1.0/ubuntu/noble'})
            builds[link + '?ws.op=getBuilds'] = [{'buildstate': s} for s in states]
        def fetch(url):
            return builds[url] if 'getBuilds' in url else publications
        output = io.StringIO()
        with patch.object(STATE, 'collection', side_effect=fetch), patch.object(
                STATE.sys, 'argv', ['script', 'owner', 'ppa', 'pkg', 'noble', '1.0-1']), contextlib.redirect_stdout(output):
            STATE.main()
        return json.loads(output.getvalue())

    def test_successful_revision_prevents_repeated_bumps(self):
        result = self.decide([(1, 'Published', ['Successfully built']),
                              (2, 'Published', ['Failed to build'])])
        self.assertEqual(result['state'], 'success')
        self.assertEqual(result['active_version'], '1.0-1~noble1~ppa1')
        self.assertEqual(result['max_n'], '2')

    def test_latest_retry_pending_is_reused(self):
        result = self.decide([(1, 'Published', ['Failed to build']),
                              (2, 'Pending', [])])
        self.assertEqual(result['state'], 'pending')
        self.assertEqual(result['active_version'], '1.0-1~noble1~ppa2')

    def test_all_architectures_must_finish(self):
        result = self.decide([(1, 'Published', ['Failed to build', 'Currently building'])])
        self.assertEqual(result['state'], 'pending')
        result = self.decide([(1, 'Published', ['Successfully built', 'Failed to build'])])
        self.assertEqual(result['state'], 'failed')

    def test_deleted_is_not_failure(self):
        self.assertEqual(self.decide([(1, 'Deleted', [])])['state'], 'unknown')

    def test_empty_is_first_upload(self):
        self.assertEqual(self.decide([])['state'], 'absent')

    def test_collection_follows_pagination(self):
        with patch.object(STATE.subprocess, 'check_output', side_effect=[
                '{"entries": [1], "next_collection_link": "https://fixture/page2"}',
                '{"entries": [2]}']) as curl:
            self.assertEqual(STATE.collection('https://fixture/page1'), [1, 2])
            self.assertEqual(curl.call_count, 2)


if __name__ == '__main__':
    unittest.main()
