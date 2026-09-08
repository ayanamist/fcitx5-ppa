import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VERSION = '5.1.15-1~noble1~ppa1'


class PreuploadCheckTest(unittest.TestCase):
    def run_build(self, status=None, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [] if status is None else [{
                'source_package_version': VERSION, 'status': status,
                'distro_series_link': 'https://api.launchpad.net/1.0/ubuntu/noble'}]
            (root / 'response').write_text(json.dumps({'entries': entries}))
            mocks = {
                'apt-get': 'mkdir fcitx5-qt-5.1.15',
                'dpkg-parsechangelog': 'echo 5.1.15-1',
                'curl': '''
case "$*" in *'Cache-Control: no-cache'*) ;; *) touch "$FIXTURE/missing-header";; esac
if [ -f "$FIXTURE/built" ]; then
  if [ "$FAIL_QUERY" = yes ]; then exit 22; fi
  cat "$FIXTURE/response"
else
  echo '{"entries": []}'
fi
''',
                'dch': 'true',
                'debuild': f'touch ../fcitx5-qt_{VERSION}.dsc ../fcitx5-qt_{VERSION}_source.changes',
                'sudo': 'touch "$FIXTURE/built"',
                'dput': 'touch "$FIXTURE/uploaded"',
            }
            for name, body in mocks.items():
                path = root / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                       FIXTURE=str(root), FAIL_QUERY='yes' if fail else 'no',
                       OWNER='test', PPA='test', SERIES='noble', GPG_KEY_ID='test',
                       GITHUB_WORKSPACE=str(ROOT), DEBFULLNAME='Test',
                       DEBEMAIL='test@example.org', GITHUB_OUTPUT=str(root / 'output'))
            result = subprocess.run(['bash', str(ROOT / 'scripts/build-and-upload.sh'),
                                     'fcitx5-qt'], env=env, capture_output=True, text=True)
            return (result, (root / 'uploaded').exists(),
                    (root / 'output').read_text() if (root / 'output').exists() else '',
                    (root / 'missing-header').exists())

    def test_version_appearing_during_build_skips_dput(self):
        for status in ('Pending', 'Published'):
            with self.subTest(status=status):
                result, uploaded, output, _ = self.run_build(status)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(uploaded)
                self.assertIn('skipped=true', output)

    def test_query_failure_does_not_upload(self):
        result, uploaded, output, _ = self.run_build(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(uploaded)
        self.assertNotIn('skipped=false', output)

    def test_unoccupied_version_uploads(self):
        result, uploaded, output, missing_header = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(uploaded)
        self.assertIn(f'uploaded_version={VERSION}', output)
        self.assertFalse(missing_header)

    def test_inactive_version_appearing_during_build_aborts(self):
        result, uploaded, _, _ = self.run_build('Deleted')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(uploaded)


if __name__ == '__main__':
    unittest.main()
