import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.1.16-1~noble1~ppa1'


class PpaVersionReuseTest(unittest.TestCase):
    def run_build(self, status, cache=False, failure=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [{'source_package_version': VERSION, 'status': status,
                        'distro_series_link': 'https://api.launchpad.net/1.0/ubuntu/noble'}]
            (root / 'history').write_text(json.dumps({'entries': entries}))
            (root / 'old').write_text(json.dumps({'entries': [{
                'source_package_version': '1.1.15-2~noble1~ppa1',
                'status': 'Published'}]}))
            mocks = {
                'apt-get': 'mkdir libime-1.1.16',
                'dpkg-parsechangelog': "echo 1.1.16-1",
                'curl': 'case "$*" in *--get*) cat "$FIXTURE/old";; *) '
                        + ('exit 22' if failure else 'cat "$FIXTURE/history"') + ';; esac',
                # Stop before packaging/signing/building; exercise real version decisions.
                'dch': 'exit 77',
            }
            for name, body in mocks.items():
                path = root / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            cache_dir = root / 'cache'
            cache_dir.mkdir()
            if cache:
                (cache_dir / 'libime.deb').touch()
            env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                       FIXTURE=str(root), OWNER='test', PPA='test', SERIES='noble',
                       GPG_KEY_ID='test', GITHUB_WORKSPACE=str(ROOT),
                       DEB_CACHE_DIR=str(cache_dir), DEBFULLNAME='Test',
                       DEBEMAIL='test@example.org')
            return subprocess.run(['bash', str(ROOT / 'scripts/build-and-upload.sh'),
                                   'libime'], env=env, text=True, capture_output=True)

    def test_active_history_reuses_version_on_cache_miss(self):
        for status in ('Pending', 'Published'):
            with self.subTest(status=status):
                result = self.run_build(status)
                self.assertIn(f'Target PPA version: {VERSION}\n', result.stdout)
                self.assertIn('rebuild for cache (no dput)', result.stdout)
                self.assertEqual(result.returncode, 77, result.stderr)

    def test_active_history_skips_on_cache_hit(self):
        result = self.run_build('Published', cache=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('cache hit; skip.', result.stdout)

    def test_inactive_history_increments(self):
        for status in ('Deleted', 'Superseded', 'Obsolete'):
            with self.subTest(status=status):
                result = self.run_build(status)
                self.assertIn('Target PPA version: 1.1.16-1~noble1~ppa2\n', result.stdout)
                self.assertNotIn('no dput', result.stdout)

    def test_history_failure_stops_version_allocation(self):
        result = self.run_build('Published', failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Target PPA version:', result.stdout)


if __name__ == '__main__':
    unittest.main()
