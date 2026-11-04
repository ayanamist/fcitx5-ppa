"""Inspect APT's real source selection at the action's first update boundary."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / '.github/actions/setup-ppa-env/action.yml'


class SetupAptSourcesTest(unittest.TestCase):
    def test_runner_vendor_repository_cannot_break_initial_update(self):
        step = next(s for s in yaml.safe_load(ACTION.read_text())['runs']['steps']
                    if s.get('name') == 'Configure Debian sid deb-src')
        for series in ['noble', 'resolute']:
            with self.subTest(series=series), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                apt = root / 'apt'
                (apt / 'sources.list.d').mkdir(parents=True)
                (apt / 'apt.conf.d').mkdir()
                (apt / 'sources.list.d/ubuntu.sources').write_text(
                    f'Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\n'
                    f'Suites: {series} {series}-updates {series}-security\n'
                    'Components: main universe\n')
                vendor = apt / 'sources.list.d/microsoft-prod.list'
                vendor.write_text(f'deb https://packages.microsoft.com/ubuntu/prod {series} main\n')
                original_vendor = vendor.read_bytes()
                config = root / 'apt.conf'
                config.write_text(f'Dir::Etc "{apt}";\nDir::State "{root}/state";\n'
                                  f'Dir::Cache "{root}/cache";\n')
                (root / 'state/lists/partial').mkdir(parents=True)
                urls = root / 'urls'
                # Stop before the first real network request. File mutations are
                # confined to the temporary APT root; APT prints its own URI list.
                sudo = root / 'sudo'
                sudo.write_text(f'#!{sys.executable}\n' + '''import os, subprocess, sys
args = sys.argv[1:]
if args[0] == 'bash':
    sys.exit(subprocess.run(args + [os.environ['TEST_APT_ROOT']]).returncode)
if args == ['apt-get', 'update']:
    result = subprocess.run(['/usr/bin/apt-get', '--print-uris', 'update'],
                            capture_output=True, text=True)
    open(os.environ['TEST_APT_URLS'], 'w').write(result.stdout + result.stderr)
    sys.exit(77 if result.returncode == 0 else result.returncode)
raise RuntimeError('Unexpected privileged command')
''')
                sudo.chmod(0o755)
                env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                           GITHUB_WORKSPACE=str(ROOT), APT_CONFIG=str(config),
                           TEST_APT_ROOT=str(apt), TEST_APT_URLS=str(urls))
                result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
                selected = urls.read_text()
                self.assertNotIn('packages.microsoft.com', selected)
                for suite in [series, f'{series}-updates', f'{series}-security']:
                    self.assertIn(f'/dists/{suite}/', selected)
                self.assertEqual(vendor.read_bytes(), original_vendor)
                # Replay the action's later Debian source entry, after the
                # archive keyring has been installed, using the same APT config.
                entry = re.search(r"sudo tee (/etc/apt/\S+debian-sid\.sources).*?<<'EOF'\n"
                                  r"(.*?)\nEOF", step['run'], re.DOTALL)
                self.assertIsNotNone(entry)
                destination = apt / entry[1].removeprefix('/etc/apt/')
                destination.write_text(entry[2] + '\n')
                selected = subprocess.check_output(['/usr/bin/apt-get', '--print-uris', 'update'],
                                                   env=env, text=True)
                self.assertIn('deb.debian.org/debian/dists/sid/InRelease', selected)
                self.assertIn('deb.debian.org/debian/dists/sid/main/source/Sources', selected)
                self.assertNotIn('deb.debian.org/debian/dists/sid/main/binary-', selected)
                self.assertNotIn('packages.microsoft.com', selected)

    def test_missing_ubuntu_sources_stops_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['bash', str(ROOT / 'scripts/configure-apt-sources.sh'),
                                     directory], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(directory) / 'apt.conf.d/99-ppa-sources').exists())


if __name__ == '__main__':
    unittest.main()
