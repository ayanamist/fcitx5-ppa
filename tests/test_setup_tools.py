"""Replay Resolute's dput conflict with the real APT resolver in an isolated repo."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / '.github/actions/setup-ppa-env/action.yml'


class SetupToolsTest(unittest.TestCase):
    def test_requested_tools_resolve_without_dput_conflict(self):
        action = yaml.safe_load(ACTION.read_text())
        step = next(s for s in action['runs']['steps']
                    if s.get('name') == 'Install build/upload tools')
        command = shlex.split(step['run'].replace('\\\n', ''))
        self.assertEqual(command[:4], ['sudo', 'apt-get', 'install', '-y'])
        requested = [arg for arg in command[4:] if not arg.startswith('-')]
        # Minimized relations from Resolute's official package metadata:
        # ubuntu-dev-tools 0.210 -> dput-ng 1.44ubuntu3 -> Conflicts: dput.
        # Other packages are neutral so this test isolates the observed conflict.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            repo.mkdir()
            package = root / 'package'
            (package / 'DEBIAN').mkdir(parents=True)
            for name in sorted(set(requested) | {'dput', 'dput-ng', 'ubuntu-dev-tools'}):
                relation = {'ubuntu-dev-tools': 'Depends: dput-ng\n',
                            'dput-ng': 'Conflicts: dput\n'}.get(name, '')
                version = {'ubuntu-dev-tools': '0.210', 'dput-ng': '1.44ubuntu3',
                           'dput': '1.2.4ubuntu4'}.get(name, '1')
                (package / 'DEBIAN/control').write_text(
                    f'Package: {name}\nVersion: {version}\nArchitecture: all\n'
                    f'{relation}Maintainer: Test <test@example.org>\n'
                    'Description: minimized resolver fixture\n')
                subprocess.run(['dpkg-deb', '--build', str(package), str(repo / f'{name}.deb')],
                               check=True, capture_output=True)
            index = subprocess.check_output(['apt-ftparchive', 'packages', '.'], cwd=repo)
            (repo / 'Packages').write_bytes(index)
            (root / 'sources.list').write_text(f'deb [trusted=yes] file:{repo} ./\n')
            (root / 'status').touch()
            (root / 'lists/partial').mkdir(parents=True)
            (root / 'cache/archives/partial').mkdir(parents=True)
            apt = ['apt-get', '-o', f'Dir::State::status={root}/status',
                   '-o', f'Dir::State::lists={root}/lists',
                   '-o', f'Dir::Cache={root}/cache',
                   '-o', f'Dir::Etc::sourcelist={root}/sources.list',
                   '-o', 'Dir::Etc::sourceparts=-', '-o', 'Debug::NoLocking=1',
                   '-o', 'APT::Sandbox::User=']
            subprocess.run(apt + ['update'], check=True, capture_output=True)
            # Verify the fixture is red-capable independently of the workflow.
            conflict = subprocess.run(apt + ['-s', 'install', 'dput', 'ubuntu-dev-tools'],
                                      text=True, capture_output=True)
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn('Conflicts: dput', conflict.stdout + conflict.stderr)
            result = subprocess.run(apt + ['-s', '--no-install-recommends',
                                          'install', *requested], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('dput', requested)
            self.assertIn('pbuilder', requested)


if __name__ == '__main__':
    unittest.main()
