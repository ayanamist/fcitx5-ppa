import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PreuploadCheckTest(unittest.TestCase):
    series = 'noble'

    @property
    def version(self):
        return f'5.1.15-1~{self.series}1~ppa1'

    def run_build(self, status=None, fail=False, initial_failures=0, final_failures=0,
                  has_series=True, local_repo=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [] if status is None else [{
                'source_package_version': self.version, 'status': status, 'self_link': 'https://fixture/source',
                'distro_series_link': f'https://api.launchpad.net/1.0/ubuntu/{self.series}'}]
            (root / 'response').write_text(json.dumps({'entries': entries}))
            mocks = {
                'apt-get': 'mkdir fcitx5-qt-5.1.15',
                'dpkg-parsechangelog': 'echo 5.1.15-1',
                'curl': '''
case "$*" in *'Cache-Control: no-cache'*) ;; *) touch "$FIXTURE/missing-header";; esac
case "$*" in *getBuilds*) echo '{"entries": [{"buildstate": "Successfully built"}]}'; exit 0;; esac
phase=initial
failures="$INITIAL_FAILURES"
if [ -f "$FIXTURE/built" ]; then phase=final; failures="$FINAL_FAILURES"; fi
count=0
if [ -f "$FIXTURE/count-$phase" ]; then count=$(cat "$FIXTURE/count-$phase"); fi
count=$((count + 1))
echo "$count" > "$FIXTURE/count-$phase"
if [ "$count" -le "$failures" ]; then exit 52; fi
if [ -f "$FIXTURE/built" ]; then
  if [ "$FAIL_QUERY" = yes ]; then exit 22; fi
  cat "$FIXTURE/response"
else
  echo '{"entries": []}'
fi
''',
                'dch': 'true',
                'debuild': f'touch ../fcitx5-qt_{self.version}.dsc ../fcitx5-qt_{self.version}_source.changes',
                'sudo': 'printf "%s\\n" "$*" >> "$FIXTURE/pbuilder-args"; touch "$FIXTURE/built"',
                'dput': 'touch "$FIXTURE/uploaded"',
            }
            for name, body in mocks.items():
                path = root / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            repo = root / 'local-repo'
            repo.mkdir()
            (repo / 'Packages').touch()
            env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                       FIXTURE=str(root), FAIL_QUERY='yes' if fail else 'no',
                       INITIAL_FAILURES=str(initial_failures), FINAL_FAILURES=str(final_failures),
                       OWNER='test', PPA='test', SERIES=self.series, GPG_KEY_ID='test',
                       GITHUB_WORKSPACE=str(ROOT), DEBFULLNAME='Test',
                       PPA_HAS_SERIES='true' if has_series else 'false',
                       PPA_ALLOW_UNTRUSTED='no', PPA_KEYRING='/fixture/ppa.gpg',
                       LOCAL_REPO_DIR=str(repo) if local_repo else '',
                       DEBEMAIL='test@example.org', GITHUB_OUTPUT=str(root / 'output'))
            result = subprocess.run(['bash', str(ROOT / 'scripts/build-and-upload.sh'),
                                     'fcitx5-qt'], env=env, capture_output=True, text=True)
            args = root / 'pbuilder-args'
            self.pbuilder_args = args.read_text() if args.exists() else ''
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

    def test_initial_empty_reply_recovers_before_upload(self):
        result, uploaded, output, _ = self.run_build(initial_failures=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(uploaded)
        self.assertIn(f'uploaded_version={self.version}', output)

    def test_preupload_empty_reply_recovers_and_skips_duplicate(self):
        result, uploaded, output, _ = self.run_build('Published', final_failures=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(uploaded)
        self.assertIn('skipped=true', output)

    def test_persistent_initial_empty_reply_does_not_allocate_or_upload(self):
        result, uploaded, output, _ = self.run_build(initial_failures=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(uploaded)
        self.assertNotIn('Target PPA version:', result.stdout)
        self.assertNotIn('skipped=false', output)

    def test_unoccupied_version_uploads(self):
        result, uploaded, output, missing_header = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(uploaded)
        self.assertIn(f'uploaded_version={self.version}', output)
        self.assertFalse(missing_header)

    def test_unpublished_series_uses_local_deps_without_missing_ppa(self):
        result, uploaded, _, _ = self.run_build(has_series=False, local_repo=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(uploaded)
        self.assertNotIn('ppa.launchpad.net', self.pbuilder_args)
        self.assertIn(f'{self.series}-updates', self.pbuilder_args)
        self.assertIn(f'{self.series}-security', self.pbuilder_args)
        self.assertIn('deb [trusted=yes] file:', self.pbuilder_args)
        self.assertIn('--bindmounts', self.pbuilder_args)

    def test_published_series_keeps_signed_ppa(self):
        result, uploaded, _, _ = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(uploaded)
        self.assertIn(f'[signed-by=/fixture/ppa.gpg] http://ppa.launchpad.net/test/test/ubuntu '
                      f'{self.series} main', self.pbuilder_args)

    def test_inactive_version_appearing_during_build_aborts(self):
        result, uploaded, _, _ = self.run_build('Deleted')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(uploaded)


class ResolutePreuploadCheckTest(PreuploadCheckTest):
    series = 'resolute'


if __name__ == '__main__':
    unittest.main()
