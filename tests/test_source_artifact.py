import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_artifact', ROOT / 'scripts/source-artifact.py')
SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE)
VERSION = '1.0-1~resolute1~ppa1'


class SourceArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key_tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.key_tmp.cleanup)
        cls.key_home = Path(cls.key_tmp.name) / 'gnupg'
        cls.key_home.mkdir(mode=0o700)
        cls.key_env = {'GNUPGHOME': str(cls.key_home), 'GPG_KEY_ID': 'fixture@example.invalid'}
        subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                        '--quick-generate-key', 'Source fixture <fixture@example.invalid>',
                        'ed25519', 'sign', '0'], env=dict(os.environ, **cls.key_env),
                       check=True, capture_output=True)
        cls.addClassCleanup(lambda: subprocess.run(
            ['gpgconf', '--homedir', str(cls.key_home), '--kill', 'gpg-agent'], capture_output=True))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'artifact'
        self.orig = self.root / 'test_1.0.orig.tar.xz'
        self.orig.write_bytes(b'original source archive fixture')
        self.component = self.root / 'test_1.0.orig-extra.tar.xz'
        self.component.write_bytes(b'additional upstream component')
        self.debian = self.root / f'test_{VERSION}.debian.tar.xz'
        self.debian.write_bytes(b'debian packaging fixture')
        self.dsc = self.root / f'test_{VERSION}.dsc'
        self.sign(self.dsc, f'Format: 3.0 (quilt)\nSource: test\nVersion: {VERSION}\n'
                  + self.checksums([self.orig, self.component, self.debian]))
        self.changes = self.root / f'test_{VERSION}_source.changes'
        self.write_changes([self.orig, self.component, self.debian, self.dsc])

    def checksums(self, paths):
        return 'Checksums-Sha256:\n' + ''.join(
            f' {hashlib.sha256(p.read_bytes()).hexdigest()} {p.stat().st_size} {p.name}\n'
            for p in paths)

    def sign(self, destination, text):
        subprocess.run(['gpg', '--batch', '--yes', '--clearsign', '--output', str(destination)],
                       input=text.encode(), env=dict(os.environ, **self.key_env),
                       check=True, capture_output=True)

    def write_changes(self, paths):
        self.sign(self.changes, f'Format: 1.8\nSource: test\nVersion: {VERSION}\n'
                  'Distribution: resolute\nArchitecture: source\n' + self.checksums(paths))

    def prepare(self):
        with patch.dict(os.environ, self.key_env):
            return SOURCE.prepare(self.output, 'test', 'resolute', '1.0-1', VERSION, self.changes)

    def test_complete_source_handoff_includes_orig_components_and_public_key(self):
        data = self.prepare()
        self.assertEqual(data['version'], VERSION)
        self.assertEqual((self.output / self.orig.name).read_bytes(), self.orig.read_bytes())
        self.assertEqual((self.output / self.component.name).read_bytes(), self.component.read_bytes())
        # The upload job can verify both signatures with only the exported public key.
        public_home = self.root / 'public-gnupg'
        public_home.mkdir(mode=0o700)
        self.addCleanup(lambda: subprocess.run(
            ['gpgconf', '--homedir', str(public_home), '--kill', 'gpg-agent'], capture_output=True))
        env = dict(os.environ, GNUPGHOME=str(public_home))
        subprocess.run(['gpg', '--batch', '--import', str(self.output / 'signing-key.asc')],
                       env=env, check=True, capture_output=True)
        for path in [self.dsc, self.changes]:
            subprocess.run(['gpg', '--batch', '--verify', str(self.output / path.name)],
                           env=env, check=True, capture_output=True)
        secret_keys = subprocess.check_output(['gpg', '--batch', '--with-colons',
                                               '--list-secret-keys'], env=env)
        self.assertNotIn(b'sec:', secret_keys)

    def test_corrupt_or_missing_payload_is_rejected(self):
        self.prepare()
        (self.output / self.orig.name).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            SOURCE.validate(self.output, 'test', 'resolute')
        (self.output / self.orig.name).unlink()
        with self.assertRaises(FileNotFoundError):
            SOURCE.validate(self.output, 'test', 'resolute')

    def test_wrong_package_series_or_version_is_rejected(self):
        self.prepare()
        for package, series in [('wrong', 'resolute'), ('test', 'noble')]:
            with self.assertRaisesRegex(ValueError, 'Wrong source package or series'):
                SOURCE.validate(self.output, package, series)
        metadata = self.output / 'source-upload.json'
        data = json.loads(metadata.read_text())
        data['version'] = '1.0-1~noble1~ppa1'
        metadata.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'Invalid PPA source version'):
            SOURCE.validate(self.output, 'test', 'resolute')

    def test_original_component_must_be_in_changes(self):
        self.write_changes([self.orig, self.debian, self.dsc])
        with self.assertRaisesRegex(ValueError, 'Missing original source input'):
            self.prepare()

    def test_unsafe_checksum_path_is_rejected(self):
        text = self.checksums([self.orig]).replace(self.orig.name, '../outside.tar.xz')
        self.sign(self.changes, f'Source: test\nVersion: {VERSION}\nDistribution: resolute\n'
                  'Architecture: source\n' + text)
        with self.assertRaisesRegex(ValueError, 'Invalid source checksum entry'):
            self.prepare()

    def test_unsigned_source_metadata_is_rejected(self):
        self.changes.write_text('Source: test\n')
        with self.assertRaisesRegex(ValueError, 'Unsigned source metadata'):
            self.prepare()

    def test_stage_plan_requires_every_package_and_omits_cache_only_rebuilds(self):
        self.prepare()
        destination = self.root / 'source-resolute-stage1-test'
        shutil.move(self.output, destination)
        SOURCE.skip(self.root / 'source-resolute-stage1-cached', 'cached', 'resolute',
                    '1.0-1', VERSION)
        plan = SOURCE.plan(self.root, 'resolute', '1', '["cached", "test"]')
        self.assertEqual([entry['package'] for entry in plan], ['test'])
        self.assertEqual(plan[0]['directory'], str(destination))
        with self.assertRaises(FileNotFoundError):
            SOURCE.plan(self.root, 'resolute', '1', '["cached", "missing", "test"]')
        with self.assertRaises(FileNotFoundError):
            SOURCE.plan(self.root, 'noble', '1', '["test"]')

    def test_stage_with_only_cached_packages_has_no_upload_work(self):
        runner = self.root / 'runner'
        for package in ['test', 'cached']:
            SOURCE.skip(runner / 'source-uploads' / f'source-resolute-stage1-{package}',
                        package, 'resolute', '1.0-1', VERSION)
        workflow = yaml.safe_load((ROOT / '.github/workflows/build-stage.yml').read_text())
        steps = workflow['jobs']['upload']['steps']
        step = next(s for s in steps if s.get('name') == 'Validate upload plan')
        env = dict(os.environ, RUNNER_TEMP=str(runner), SERIES='resolute', STAGE='1',
                   PACKAGES='["test", "cached"]', GITHUB_OUTPUT=str(runner / 'output'))
        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((runner / 'upload-plan.json').read_text()), [])
        self.assertEqual((runner / 'output').read_text(), 'upload_required=false\n')
        for name in ['Install upload tools', 'Verify and upload sources in package order']:
            action = next(s for s in steps if s.get('name') == name)
            self.assertEqual(action['if'], "steps.plan.outputs.upload_required == 'true'")

    def test_single_flat_download_runs_the_workflow_plan_for_both_series(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/build-stage.yml').read_text())
        step = next(s for s in workflow['jobs']['upload']['steps']
                    if s.get('name') == 'Validate upload plan')
        for series in ['noble', 'resolute']:
            with self.subTest(series=series):
                runner = self.root / series
                # download-artifact@v8 flattens a sole pattern match, even with
                # merge-multiple false. Stage 2 and Stage 4 each match one artifact.
                SOURCE.skip(runner / 'source-uploads', 'fcitx5', series, '5.1.23-1',
                            f'5.1.23-1~{series}1~ppa1')
                env = dict(os.environ, RUNNER_TEMP=str(runner), SERIES=series, STAGE='2',
                           PACKAGES='["fcitx5"]', GITHUB_OUTPUT=str(runner / 'output'))
                result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
                                        cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads((runner / 'upload-plan.json').read_text()), [])
                self.assertEqual((runner / 'output').read_text(), 'upload_required=false\n')

    def test_single_signed_source_accepts_flat_and_named_downloads(self):
        self.prepare()
        flat_plan = SOURCE.plan(self.output, 'resolute', '2', '["test"]')
        self.assertEqual([entry['package'] for entry in flat_plan], ['test'])
        self.assertEqual(flat_plan[0]['directory'], str(self.output))
        named_root = self.root / 'named-download'
        nested = named_root / 'source-resolute-stage2-test'
        shutil.copytree(self.output, nested)
        named_plan = SOURCE.plan(named_root, 'resolute', '2', '["test"]')
        self.assertEqual([entry['package'] for entry in named_plan], ['test'])
        self.assertEqual(named_plan[0]['directory'], str(nested))

    def test_flat_download_cannot_hide_missing_packages_or_invalid_metadata(self):
        self.prepare()
        with self.assertRaises(FileNotFoundError):
            SOURCE.plan(self.output, 'resolute', '2', '["test", "missing"]')
        for series, packages in [('noble', '["test"]'), ('resolute', '["wrong"]')]:
            with self.subTest(series=series, packages=packages):
                with self.assertRaisesRegex(ValueError, 'Wrong source package or series'):
                    SOURCE.plan(self.output, series, '2', packages)
        (self.output / self.orig.name).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            SOURCE.plan(self.output, 'resolute', '2', '["test"]')

    def test_stage_plan_preserves_package_order(self):
        self.prepare()
        shutil.move(self.output, self.root / 'source-resolute-stage1-test')
        # Build a second independent signed source, not a renamed metadata record.
        original = self.root / 'other_1.0.orig.tar.xz'
        original.write_bytes(b'other upstream source')
        descriptor = self.root / f'other_{VERSION}.dsc'
        self.sign(descriptor, f'Source: other\nVersion: {VERSION}\n' + self.checksums([original]))
        changes = self.root / f'other_{VERSION}_source.changes'
        self.sign(changes, f'Source: other\nVersion: {VERSION}\nDistribution: resolute\n'
                  'Architecture: source\n' + self.checksums([original, descriptor]))
        with patch.dict(os.environ, self.key_env):
            SOURCE.prepare(self.root / 'source-resolute-stage1-other', 'other', 'resolute',
                           '1.0-1', VERSION, changes)
        plan = SOURCE.plan(self.root, 'resolute', '1', '["other", "test"]')
        self.assertEqual([entry['package'] for entry in plan], ['other', 'test'])
        # Execute the workflow's actual upload loop with a network-upload stub.
        # GPG import/trust runs against a fresh keyring containing no private keys.
        runner = self.root / 'runner'
        runner.mkdir()
        (runner / 'upload-plan.json').write_text(json.dumps(plan))
        mocks = self.root / 'upload-bin'
        mocks.mkdir()
        commands = {
            'curl': 'echo \'{"entries":[]}\'',
            'dput': '''gpg --batch --with-colons --list-secret-keys | \
awk -F: '$1 == "sec" {found=1} END {exit found ? 1 : 0}' || exit 99
printf '%s\\n' "$2" >> "$FIXTURE/upload-order"
''',
        }
        for name, body in commands.items():
            path = mocks / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)
        workflow = yaml.safe_load((ROOT / '.github/workflows/build-stage.yml').read_text())
        step = next(s for s in workflow['jobs']['upload']['steps']
                    if s.get('name') == 'Verify and upload sources in package order')
        env = dict(os.environ, PATH=f'{mocks}:{os.environ["PATH"]}', FIXTURE=str(self.root),
                   RUNNER_TEMP=str(runner), GITHUB_WORKSPACE=str(ROOT),
                   OWNER='owner', PPA='ppa', SERIES='resolute')
        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / 'upload-order').read_text().splitlines(),
                         [changes.name, self.changes.name])

    def test_build_defers_dput_and_upload_rechecks_history_after_waiting(self):
        mocks = self.root / 'bin'
        mocks.mkdir()
        commands = {
            'apt-get': '''mkdir -p test-1.0/debian
printf 'Source: test\\nBuild-Depends: cmake\\n' > test-1.0/debian/control''',
            'dpkg-parsechangelog': 'echo 1.0-1',
            'dch': 'true',
            'debuild': 'cp "$FIXTURE"/test_* ..',
            'sudo': 'touch "$FIXTURE/built"',
            'dput': 'touch "$FIXTURE/uploaded"',
            'curl': '''case "$*" in
*getBuilds*) echo '{"entries":[{"buildstate":"Successfully built"}]}' ;;
*) if [ -f "$FIXTURE/final-history" ]; then cat "$FIXTURE/final-history";
   else echo '{"entries":[]}'; fi ;;
esac''',
        }
        for name, body in commands.items():
            path = mocks / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)
        env = dict(os.environ, **self.key_env, PATH=f'{mocks}:{os.environ["PATH"]}',
                   FIXTURE=str(self.root), OWNER='owner', PPA='ppa', SERIES='resolute',
                   GITHUB_WORKSPACE=str(ROOT), GITHUB_OUTPUT=str(self.root / 'output'),
                   SOURCE_ARTIFACT_DIR=str(self.output), DEB_CACHE_DIR='',
                   DEBEMAIL='fixture@example.invalid', DEBFULLNAME='Fixture')
        result = subprocess.run(['bash', str(ROOT / 'scripts/build-and-upload.sh'), 'test'],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / 'uploaded').exists())
        self.assertIs(SOURCE.validate(self.output, 'test', 'resolute')['upload_required'], True)
        # A package appeared while this upload job was waiting for the PPA lock.
        (self.root / 'final-history').write_text(json.dumps({'entries': [{
            'source_package_version': VERSION, 'status': 'Published',
            'distro_series_link': 'https://api.launchpad.net/1.0/ubuntu/resolute',
            'self_link': 'https://fixture/source',
        }]}))
        result = subprocess.run(['bash', str(ROOT / 'scripts/upload-source.sh'),
                                 'test', '1.0-1', VERSION, str(self.output / self.changes.name)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / 'uploaded').exists())
        self.assertIn('skip duplicate upload', result.stdout)
        # An existing version with a missing deb cache rebuilds only the cache.
        # It must emit an explicit no-upload artifact for the stage coordinator.
        cache_only = self.root / 'cache-only'
        env['SOURCE_ARTIFACT_DIR'] = str(cache_only)
        result = subprocess.run(['bash', str(ROOT / 'scripts/build-and-upload.sh'), 'test'],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = SOURCE.validate(cache_only, 'test', 'resolute')
        self.assertIs(data['upload_required'], False)
        self.assertFalse((self.root / 'uploaded').exists())


if __name__ == '__main__':
    unittest.main()
