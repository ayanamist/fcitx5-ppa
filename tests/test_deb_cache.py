import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('deb_cache', ROOT / 'scripts/deb-cache.py')
CACHE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CACHE)
VERSION = '1.0-1~noble1~ppa1'


class DebCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'
        self.files = {}
        for name, arch in [('libtest-dev', 'amd64'), ('test-data', 'all')]:
            package = self.root / name
            (package / 'DEBIAN').mkdir(parents=True)
            (package / 'DEBIAN/control').write_text(
                f'Package: {name}\nSource: test\nVersion: {VERSION}\nArchitecture: {arch}\n'
                'Maintainer: Test <test@example.org>\nDescription: fixture\n')
            output = self.root / f'{name}_{VERSION}_{arch}.deb'
            subprocess.run(['dpkg-deb', '--build', str(package), str(output)], check=True, capture_output=True)
            self.files[output.name] = output.read_bytes()
        self.changes = f'Source: test\nVersion: {VERSION}\nChecksums-Sha256:\n' + ''.join(
            f' {hashlib.sha256(data).hexdigest()} {len(data)} {name}\n' for name, data in self.files.items())
        self.build = {'buildstate': 'Successfully built', 'arch_tag': 'amd64',
                      'changesfile_url': 'https://fixture/build.changes'}

    def fetch(self, url, path):
        from urllib.parse import unquote
        name = unquote(url.rsplit('/', 1)[1])
        path.write_bytes(self.changes.encode() if name == 'build.changes' else self.files[name])

    def restore(self, builds=None):
        return CACHE.restore(self.cache, 'test', VERSION, 'amd64',
                             [self.build] if builds is None else builds)

    def test_downloads_all_debs_and_reuses_verified_cache_offline(self):
        with patch.object(CACHE, 'fetch', side_effect=self.fetch) as fetch:
            self.assertTrue(self.restore())
            self.assertEqual(fetch.call_count, 3)
        with patch.object(CACHE, 'fetch', side_effect=AssertionError('network used')):
            self.assertTrue(self.restore([]))
        self.assertEqual(len(list(self.cache.glob('*.deb'))), 2)

    def test_partial_cache_fetches_only_missing_deb(self):
        self.cache.mkdir()
        name, data = next(iter(self.files.items()))
        (self.cache / name).write_bytes(data)
        with patch.object(CACHE, 'fetch', side_effect=self.fetch) as fetch:
            self.assertTrue(self.restore())
            self.assertEqual(fetch.call_count, 2)

    def test_wrong_arch_or_unfinished_build_falls_back(self):
        for change in ({'arch_tag': 'arm64'}, {'buildstate': 'Currently building'}):
            with patch.object(CACHE, 'fetch', side_effect=AssertionError('network used')):
                self.assertFalse(self.restore([{**self.build, **change}]))

    def test_bad_checksum_does_not_publish_partial_cache(self):
        def corrupt(url, path):
            self.fetch(url, path)
            if url.endswith('.deb'):
                path.write_bytes(b'corrupt')
        with patch.object(CACHE, 'fetch', side_effect=corrupt), self.assertRaises(ValueError):
            self.restore()
        self.assertFalse((self.cache / '.manifest.json').exists())
        self.assertFalse(self.cache.exists())

    def test_network_failure_preserves_existing_cache(self):
        self.cache.mkdir()
        sentinel = self.cache / 'existing.deb'
        sentinel.write_bytes(b'old')
        with patch.object(CACHE, 'fetch', side_effect=OSError('network')), self.assertRaises(OSError):
            self.restore()
        self.assertEqual(sentinel.read_bytes(), b'old')

    def test_wrong_version_and_path_traversal_are_rejected(self):
        for contents in (self.changes.replace(VERSION, '2.0-1'),
                         self.changes.replace('libtest-dev_', '../libtest-dev_')):
            with patch.object(CACHE, 'fetch', side_effect=lambda url, path: path.write_text(contents)), self.assertRaises(ValueError):
                self.restore()

    def test_deb_metadata_is_checked_independently_of_hash(self):
        path = self.root / next(iter(self.files))
        for package, version, arch in [('wrong', VERSION, 'amd64'),
                                       ('test', '2.0-1', 'amd64'),
                                       ('test', VERSION, 'arm64')]:
            with self.assertRaises(ValueError):
                CACHE.validate(path, package, version, arch)

    def test_extra_old_deb_invalidates_cache(self):
        with patch.object(CACHE, 'fetch', side_effect=self.fetch):
            self.assertTrue(self.restore())
        (self.cache / 'old.deb').write_bytes(b'old revision')
        self.assertFalse(self.restore([]))

    def test_corrupted_manifest_cache_is_not_a_hit(self):
        with patch.object(CACHE, 'fetch', side_effect=self.fetch):
            self.assertTrue(self.restore())
        next(self.cache.glob('*.deb')).write_bytes(b'corrupt')
        self.assertFalse(self.restore([]))


if __name__ == '__main__':
    unittest.main()
