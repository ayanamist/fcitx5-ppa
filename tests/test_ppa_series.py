import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / '.github/actions/setup-ppa-env/action.yml'


class PpaSeriesTest(unittest.TestCase):
    def check_index(self, status='200', exit_code=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curl = root / 'curl'
            curl.write_text(f'#!/bin/sh\nprintf "%s" "{status}"\nexit {exit_code}\n')
            curl.chmod(0o755)
            return subprocess.run(['bash', str(ROOT / 'scripts/check-ppa-series.sh'),
                                   'owner', 'ppa', 'resolute'],
                                  env=dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}'),
                                  capture_output=True, text=True)

    def test_published_index_is_used(self):
        result = self.check_index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'true\n')

    def test_only_not_found_means_empty_series(self):
        result = self.check_index('404')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'false\n')
        for status in ['403', '500', '503', '000', '']:
            with self.subTest(status=status):
                result = self.check_index(status)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('false', result.stdout)

    def test_transport_error_is_not_empty_series(self):
        result = self.check_index('000', 52)
        self.assertEqual(result.returncode, 52)
        self.assertEqual(result.stdout, '')

    def test_chroot_omits_only_unpublished_ppa_mirror(self):
        action = yaml.safe_load(ACTION.read_text())
        step = next(s for s in action['runs']['steps']
                    if s.get('name') == 'Create pbuilder chroot if missing')
        for series in ['noble', 'resolute']:
            for has_index in ['true', 'false']:
                with self.subTest(series=series, has_index=has_index), \
                        tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    # Capture the actual generated pbuilderrc; no root operations.
                    mocks = {'sudo': 'if [ "$1" = tee ]; then cat > "$CAPTURE"; fi',
                             'mkdir': 'exit 0'}
                    for name, body in mocks.items():
                        path = root / name
                        path.write_text('#!/bin/sh\n' + body + '\n')
                        path.chmod(0o755)
                    capture = root / 'pbuilderrc'
                    env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                               CAPTURE=str(capture), SERIES=series, OWNER='owner', PPA='ppa',
                               PPA_ALLOW_UNTRUSTED='no', PPA_KEYRING='/fixture/ppa.gpg',
                               PPA_HAS_SERIES=has_index)
                    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    config = capture.read_text()
                    self.assertIn(f'{series}-updates main universe', config)
                    self.assertIn(f'{series}-security main universe', config)
                    self.assertIn('ALLOWUNTRUSTED=no', config)
                    if has_index == 'false':
                        self.assertNotIn('ppa.launchpad.net', config)
                    else:
                        self.assertIn(f'[signed-by=/fixture/ppa.gpg] http://ppa.launchpad.net/'
                                      f'owner/ppa/ubuntu {series} main', config)


if __name__ == '__main__':
    unittest.main()
