import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from build_deps_fixture import SOURCES, local_packages, source_control

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/pin-build-deps.py'
SPEC = importlib.util.spec_from_file_location('pin_build_deps', SCRIPT)
PIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIN)


class PinBuildDepsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.control = self.root / 'control'
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.expected = self.root / 'expected.json'

    def run_pin(self, package='fcitx5-chinese-addons', series='resolute', packages=None):
        (self.repo / 'Packages').write_text(local_packages(series) if packages is None else packages)
        return subprocess.run(['python3', str(SCRIPT), package, series, str(self.control),
                               str(self.repo), str(self.expected)], capture_output=True, text=True)

    def test_all_tracked_build_dependencies_are_mapped_and_pinned_for_both_series(self):
        owners = {binary: source for source, fields in SOURCES.items()
                  for binary in fields['Binary'].split(', ')}
        for series in ('noble', 'resolute'):
            for package in SOURCES:
                with self.subTest(series=series, package=package):
                    original = source_control(package)
                    actual = {}
                    for field in PIN.BUILD_FIELD.finditer(original.split('\n\n')[0]):
                        for token in re.split(r'[,|]', field[2]):
                            atom = PIN.ATOM.fullmatch(token)
                            self.assertIsNotNone(atom, token)
                            if atom['name'] in owners:
                                actual[atom['name']] = owners[atom['name']]
                    self.assertEqual(PIN.mapping(package), actual)
                    self.control.write_text(original)
                    result = self.run_pin(package, series)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    expected = json.loads(self.expected.read_text())
                    self.assertEqual(set(expected), set(actual))
                    for binary, source in actual.items():
                        floor = SOURCES[source]['Version'].rsplit('-', 1)[0]
                        self.assertEqual(expected[binary], floor)
                        self.assertIn(f'{binary} (>= {floor})', self.control.read_text())
                    first = self.control.read_bytes()
                    result = self.run_pin(package, series)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.control.read_bytes(), first, 'Pinning must be idempotent')

    def test_restrictions_alternatives_and_binary_dependencies_are_preserved(self):
        text = ('Source: test\n'
                'Build-Depends: libimecore-dev:any (>= 1.1.14) [linux-any] <!nocheck> | fallback,\n'
                ' other-dev (>= 9)\n'
                'Build-Depends-Arch: libimecore-dev:native\n'
                'Build-Depends-Indep: libimecore-dev (>= 99:1.0) <docs>\n\n'
                'Package: test\nDepends: libimecore-dev (>= 1.0)\n'
                'Description: libimecore-dev should stay unchanged\n')
        pinned, expected = PIN.pin_control(text, {'libimecore-dev': '2:1.1.17-1~resolute1~ppa2'})
        self.assertIn('libimecore-dev:any (>= 2:1.1.17) '
                      '[linux-any] <!nocheck> | fallback', pinned)
        self.assertIn('Build-Depends-Arch: libimecore-dev:native (>= 2:1.1.17)', pinned)
        self.assertIn('Build-Depends-Indep: libimecore-dev (>= 99:1.0) <docs>', pinned)
        self.assertEqual(expected['libimecore-dev'], '99:1.0')
        self.assertEqual(pinned.split('\n\n')[1], text.split('\n\n')[1])
        self.assertIn('other-dev (>= 9)', pinned)

    def test_stronger_upstream_constraints_are_not_weakened(self):
        for relation in ('>=', '>>', '='):
            text = f'Source: test\nBuild-Depends: libimecore-dev ({relation} 2:1.0)\n'
            pinned, expected = PIN.pin_control(text, {'libimecore-dev': '1:9.0'})
            self.assertEqual(pinned, text)
            self.assertEqual(expected, {'libimecore-dev': '2:1.0'})

    def test_only_packaging_suffixes_are_removed_from_generated_floor(self):
        cases = {
            '1.1.17-1~resolute1~ppa1': '1.1.17',
            '1.1.17-2ubuntu3~noble1~ppa10+b1': '1.1.17',
            '2:1.1.17-1~resolute1~ppa1+b1': '2:1.1.17',
            '3.2.1+ds-3~noble1~ppa1': '3.2.1+ds',
            '1.17.0+dfsg2-3~resolute1~ppa1': '1.17.0+dfsg2',
            '1.2~rc1-1~resolute1~ppa1': '1.2~rc1',
            '1.1.17~resolute1~ppa1': '1.1.17',
            '1.1.17': '1.1.17',
        }
        for version, floor in cases.items():
            with self.subTest(version=version):
                text = 'Source: test\nBuild-Depends: libimecore-dev\n'
                pinned, expected = PIN.pin_control(text, {'libimecore-dev': version})
                self.assertIn(f'libimecore-dev (>= {floor})', pinned)
                self.assertEqual(expected, {'libimecore-dev': floor})

    def test_existing_debian_revision_requirement_is_preserved(self):
        text = 'Source: test\nBuild-Depends: libimecore-dev (>= 1.1.17-2~)\n'
        pinned, expected = PIN.pin_control(text, {'libimecore-dev': '1.1.17-3~resolute1~ppa1'})
        self.assertEqual(pinned, text)
        self.assertEqual(expected, {'libimecore-dev': '1.1.17-2~'})

    def test_conflicting_constraints_fail_before_modifying_control(self):
        for relation, version in (('=', '1.0'), ('<<', '9.0'), ('<=', '9.0')):
            with self.subTest(relation=relation):
                text = f'Source: libime\nBuild-Depends: libfcitx5utils-dev ({relation} {version})\n'
                self.control.write_text(text)
                result = self.run_pin('libime')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('cannot raise', result.stderr)
                self.assertEqual(self.control.read_text(), text)
                self.assertFalse(self.expected.exists())

    def test_missing_or_wrong_series_artifacts_fail_without_partial_pinning(self):
        text = source_control('fcitx5-chinese-addons')
        for packages in ('', local_packages('noble'), local_packages('resolute').replace(
                'Package: libimecore-dev\nSource: libime', 'Package: libimecore-dev\nSource: wrong')):
            with self.subTest(packages=packages[:80]):
                self.control.write_text(text)
                result = self.run_pin(packages=packages)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.control.read_text(), text)
                self.assertFalse(self.expected.exists())

    def test_missing_repo_fails_for_internal_dependencies(self):
        self.control.write_text(source_control('libime'))
        result = subprocess.run(['python3', str(SCRIPT), 'libime', 'resolute', str(self.control),
                                 '', str(self.expected)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LOCAL_REPO_DIR is required', result.stderr)
        self.assertFalse(self.expected.exists())

    def test_epochs_binary_versions_and_revision_order_are_preserved(self):
        packages = ''
        for version, arch in [('1.1.17-1~resolute1~ppa9', 'amd64'),
                              ('1.1.17-1~resolute1~ppa10', 'amd64'),
                              ('2:1.1.17-1~resolute1~ppa1+b1', 'all'),
                              ('99:1.1.17-1~resolute1~ppa1', 'arm64')]:
            packages += (f'Package: libimecore-dev\nVersion: {version}\nArchitecture: {arch}\n'
                         'Source: libime (1.1.17-1~resolute1~ppa1)\n\n')
        self.assertEqual(PIN.resolve_versions(packages, {'libimecore-dev': 'libime'},
                                             'resolute', 'amd64'),
                         {'libimecore-dev': '2:1.1.17-1~resolute1~ppa1+b1'})
        without_epoch = packages.split('Package: libimecore-dev\nVersion: 2:')[0]
        self.assertEqual(PIN.resolve_versions(without_epoch, {'libimecore-dev': 'libime'},
                                             'resolute', 'amd64'),
                         {'libimecore-dev': '1.1.17-1~resolute1~ppa10'})

    def test_absent_mapping_entry_warns_without_inventing_dependency(self):
        text = 'Source: libime\nBuild-Depends: cmake\n'
        self.control.write_text(text)
        result = self.run_pin('libime', packages='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('not in Build-Depends; skip', result.stderr)
        self.assertEqual(self.control.read_text(), text)
        self.assertEqual(json.loads(self.expected.read_text()), {})

    def test_real_source_package_carries_floor_and_dpkg_rejects_old_dependency(self):
        source = self.root / 'fcitx5-chinese-addons-5.1.15'
        debian = source / 'debian'
        (debian / 'source').mkdir(parents=True)
        (source / 'README').write_text('Source packaging regression fixture\n')
        with tarfile.open(self.root / 'fcitx5-chinese-addons_5.1.15.orig.tar.xz', 'w:xz') as archive:
            archive.add(source, arcname=source.name, filter=lambda info: None if '/debian' in info.name else info)
        shutil.copy(ROOT / 'tests/fixtures/debian-sid/fcitx5-chinese-addons/debian/control', debian / 'control')
        (debian / 'source/format').write_text('3.0 (quilt)\n')
        (debian / 'rules').write_text('#!/usr/bin/make -f\n%:\n\tdh $@\n')
        (debian / 'rules').chmod(0o755)
        (debian / 'changelog').write_text(
            'fcitx5-chinese-addons (5.1.15-1~resolute1~ppa3) resolute; urgency=medium\n\n'
            '  * Fixture.\n\n -- Test <test@example.org>  Thu, 05 Nov 2026 16:00:00 +0000\n')
        self.control = debian / 'control'
        result = self.run_pin()
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(['dpkg-source', '-b', str(source)], cwd=self.root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        dsc = (self.root / 'fcitx5-chinese-addons_5.1.15-1~resolute1~ppa3.dsc').read_text()
        dependency = 'libimecore-dev (>= 1.1.17)'
        self.assertIn(dependency, dsc)
        self.assertIn('libimepinyin-dev (>= 1.1.17)', dsc)
        self.assertNotIn('libimecore-dev (>= 1.1.17-', dsc)
        admindir = self.root / 'dpkg'
        admindir.mkdir()
        for version, expected_code in [('1.1.14-1', 1), ('1.1.17-1~resolute1~ppa1', 0),
                                       ('1.1.17-1', 0), ('1.1.17-2ubuntu1', 0)]:
            (admindir / 'status').write_text(
                'Package: libimecore-dev\nStatus: install ok installed\n'
                f'Architecture: amd64\nVersion: {version}\n'
                'Maintainer: Test <test@example.org>\nDescription: fixture\n\n')
            check = subprocess.run(['dpkg-checkbuilddeps', '--admindir', str(admindir),
                                    '-I', '-d', dependency, str(self.control)],
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, expected_code, check.stdout + check.stderr)
            if expected_code:
                self.assertIn(dependency, check.stderr)


if __name__ == '__main__':
    unittest.main()
