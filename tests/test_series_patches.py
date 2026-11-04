"""Run the packaging patch path against pinned Debian sources, without a build."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/debian-sid'


class SeriesPatchesTest(unittest.TestCase):
    def check_package(self, package, series):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mocks = {
                'apt-get': 'cp -r "$SOURCE_FIXTURE" "${PACKAGE}-fixture"',
                'dpkg-parsechangelog': 'echo 1.0-1',
                'curl': 'echo \'{"entries": []}\'',
                # Capture the source at the signing boundary; use the real patch command.
                'dch': 'cp -r . "$PATCHED_SOURCE"; exit 77',
            }
            for name, body in mocks.items():
                path = root / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            output = root / 'patched'
            env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                       SOURCE_FIXTURE=str(FIXTURES / package), PACKAGE=package,
                       PATCHED_SOURCE=str(output), OWNER='test', PPA='test',
                       SERIES=series, GPG_KEY_ID='test', GITHUB_WORKSPACE=str(ROOT),
                       DEBFULLNAME='Test', DEBEMAIL='test@example.org')
            result = subprocess.run(['bash', str(ROOT / 'scripts/build-and-upload.sh'),
                                     package], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
            control = (output / 'debian/control').read_text()
            if package != 'librime':
                self.assertIn('debhelper-compat (= 13)', control)
                self.assertNotIn('debhelper-compat (= 14)', control)
                for stanza in control.split('\n\n'):
                    if 'Architecture: any\n' in stanza:
                        name = stanza.splitlines()[0]
                        self.assertIn('${shlibs:Depends}', stanza, name)
                        self.assertIn('${misc:Depends}', stanza, name)

            if series == 'resolute':
                self.assertNotIn('noble debian patch', result.stdout)
                self.assertFalse((output / 'debian/patches/series').exists())
                if package == 'librime':
                    self.assertEqual(control, (FIXTURES / package / 'debian/control').read_text())
                if package == 'fcitx5':
                    self.assertEqual(control.count('plasma-wayland-protocols (>= 1.20)'), 2)
                    self.assertNotIn(' gcc-14,', control)
                    self.assertNotIn(' g++-14,', control)
                    self.assertEqual((output / 'debian/rules').read_bytes(),
                                     (FIXTURES / package / 'debian/rules').read_bytes())
                    self.assertEqual((output / 'CMakeLists.txt').read_bytes(),
                                     (FIXTURES / package / 'CMakeLists.txt').read_bytes())
                if package == 'fcitx5-chinese-addons':
                    self.assertIn('libopencc-dev (>= 1.1.9~)', control)
            else:
                if package == 'librime':
                    for dependency in ['libmarisa-dev', 'libopencc-dev',
                                       'libutfcpp-dev', 'libyaml-cpp-dev']:
                        self.assertIn(f' {dependency},\n', control)
                if package == 'fcitx5-chinese-addons':
                    self.assertIn(' libopencc-dev,\n', control)
                if package == 'fcitx5':
                    self.assertNotIn('plasma-wayland-protocols', control)
                    for compiler in ['gcc-14', 'g++-14']:
                        self.assertIn(f' {compiler},\n', control)
                        self.assertIn(compiler, (output / 'debian/rules').read_text())
                    patches = (output / 'debian/patches/series').read_text().splitlines()
                    self.assertEqual(patches, ['relax-wayland-protocols-version.patch'])
                    for patch in patches:
                        command = ['patch', '-p1', '--fuzz=0', '-i',
                                   str(output / 'debian/patches' / patch)]
                        subprocess.run(command + ['--dry-run'], cwd=output,
                                       check=True, capture_output=True)
                        subprocess.run(command, cwd=output, check=True, capture_output=True)
                    cmake = (output / 'CMakeLists.txt').read_text()
                    self.assertIn('find_package(WaylandProtocols 1.45 REQUIRED)', cmake)
                    self.assertIn('option(USE_SYSTEM_PLASMA_WAYLAND_PROTOCOLS '
                                  '"Use system installed plasma protocols" Off)', cmake)

    def test_series_specific_packaging(self):
        for series in ['noble', 'resolute']:
            for package in ['fcitx5', 'fcitx5-chinese-addons', 'fcitx5-lua', 'librime']:
                with self.subTest(series=series, package=package):
                    self.check_package(package, series)


if __name__ == '__main__':
    unittest.main()
