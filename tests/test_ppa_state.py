import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'ppa_state', Path(__file__).resolve().parents[1] / 'scripts/ppa-state.py')
STATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATE)


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
