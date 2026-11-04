"""Replay the immutable-cache regression using the workflow's transfer steps.

Requires PyYAML (python3-yaml).
"""
from pathlib import Path
import fnmatch
import json
import shutil
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/build-ppa.yml'
PIPELINE = WORKFLOW.with_name('build-series.yml')
STAGE_PIPELINE = WORKFLOW.with_name('build-stage.yml')


class DebHandoffTest(unittest.TestCase):
    def test_series_failures_have_separate_stage_dependencies(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        calls = [j for j in workflow['jobs'].values() if 'uses' in j]
        self.assertEqual(len(calls), 1, 'Each series needs its own four-stage workflow call')
        call = calls[0]
        self.assertNotIn('needs', call)
        self.assertFalse(call['strategy']['fail-fast'])
        self.assertEqual(call['strategy']['max-parallel'], 2)
        self.assertEqual(call['strategy']['matrix']['series'], ['noble', 'resolute'])
        self.assertEqual(call['with']['series'], '${{ matrix.series }}')
        self.assertNotIn('continue-on-error', call)
        pipeline = yaml.safe_load((WORKFLOW.parents[2] / call['uses'].removeprefix('./')).read_text())
        for index in range(1, 5):
            job = pipeline['jobs'][f'stage{index}']
            self.assertNotIn('strategy', job, 'Each call must represent the complete stage')
            self.assertEqual(job.get('needs'), f'stage{index - 1}' if index > 1 else None)
            self.assertNotIn('if', job, 'Failed upstream builds must still block their own downstream')

    def test_only_source_uploads_share_the_ppa_lock(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        pipeline = yaml.safe_load(PIPELINE.read_text())
        package = yaml.safe_load(STAGE_PIPELINE.read_text())
        self.assertNotIn('concurrency', package['jobs']['build'])
        upload = package['jobs']['upload']
        self.assertEqual(upload['needs'], 'build')
        lock = upload['concurrency']
        self.assertEqual(lock['group'], 'ppa-transfer-${{ vars.OWNER }}-${{ vars.PPA }}')
        self.assertNotIn('series', lock['group'])
        self.assertNotIn('package', lock['group'])
        self.assertNotEqual(lock['group'], workflow['concurrency']['group'])
        self.assertFalse(lock['cancel-in-progress'])
        # One parent run, two series, one stage call per series: at most
        # two nonterminal upload jobs, fitting GitHub's one-running/one-pending queue.
        self.assertEqual(workflow['jobs']['build']['strategy']['max-parallel'], 2)
        for stage in pipeline['jobs'].values():
            self.assertEqual(stage['uses'], './.github/workflows/build-stage.yml')
        self.assertNotIn('strategy', upload, 'One upload job serializes the whole stage')
        self.assertFalse(package['jobs']['build']['strategy']['fail-fast'])
        self.assertGreaterEqual(package['jobs']['build']['strategy'].get('max-parallel', 4), 4)
        self.assertEqual(package['jobs']['build']['strategy']['matrix']['package'],
                         '${{ fromJSON(inputs.packages) }}')

    def test_each_series_uses_its_own_runner_cache_and_four_stages(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        self.assertEqual(workflow['concurrency']['group'], 'ppa-upload')
        self.assertFalse(workflow['concurrency']['cancel-in-progress'])
        call = workflow['jobs']['build']
        self.assertEqual(call['strategy']['matrix']['include'], [
            {'series': 'noble', 'runner': 'ubuntu-24.04'},
            {'series': 'resolute', 'runner': 'ubuntu-26.04'},
        ])
        self.assertEqual(call['with']['runner'], '${{ matrix.runner }}')
        self.assertEqual(call['uses'], './.github/workflows/build-series.yml')
        self.assertEqual(set(call['secrets']), {'GPG_KEY_ID', 'GPG_PRIVATE_KEY', 'GPG_PASSPHRASE'})
        # PyYAML's YAML 1.1 loader reads the unquoted "on" key as True.
        triggers = workflow.get('on', workflow.get(True))
        self.assertIn('.github/workflows/build-series.yml', triggers['push']['paths'])
        self.assertIn('.github/workflows/build-stage.yml', triggers['push']['paths'])
        pipeline = yaml.safe_load(PIPELINE.read_text())
        package_pipeline = yaml.safe_load(STAGE_PIPELINE.read_text())
        self.assertEqual(package_pipeline['env']['SERIES'], '${{ inputs.series }}')
        self.assertNotIn('concurrency', pipeline,
                         'A child concurrency group can cancel or deadlock its caller')
        expected = {
            'stage1': ['librime', 'yoga', 'fcitx5-gtk'],
            'stage2': ['fcitx5'],
            'stage3': ['libime', 'fcitx5-lua', 'fcitx5-qt', 'fcitx5-rime'],
            'stage4': ['fcitx5-chinese-addons'],
        }
        for index, (name, packages) in enumerate(expected.items(), start=1):
            job = pipeline['jobs'][name]
            self.assertEqual(job.get('needs'), f'stage{index - 1}' if index > 1 else None)
            self.assertEqual(job['uses'], './.github/workflows/build-stage.yml')
            self.assertEqual(json.loads(job['with']['packages']), packages)
            self.assertEqual(job['with'], {'packages': json.dumps(packages), 'stage': index,
                                          'series': '${{ inputs.series }}',
                                          'runner': '${{ inputs.runner }}',
                                          'upstream': index > 1})
            self.assertEqual(job['secrets'], 'inherit')
        build = package_pipeline['jobs']['build']
        self.assertEqual(build['runs-on'], '${{ inputs.runner }}')
        setup = next(s for s in build['steps']
                     if s.get('uses') == './.github/actions/setup-ppa-env')
        self.assertEqual(setup['with']['series'], '${{ env.SERIES }}')
        cache = next(s for s in build['steps'] if s.get('name') == 'Cache own debs')
        self.assertEqual(cache['with']['key'],
                         'debs-${{ matrix.package }}-${{ env.SERIES }}-'
                         '${{ steps.ver.outputs.deb_version }}')
        download = next(s for s in build['steps'] if s.get('name') == 'Download upstream debs')
        self.assertEqual(download['if'], 'inputs.upstream')
        upload_steps = package_pipeline['jobs']['upload']['steps']
        source = next(s for s in upload_steps if s.get('name') == 'Download source artifacts')
        self.assertEqual(source['with']['pattern'],
                         'source-${{ env.SERIES }}-stage${{ inputs.stage }}-*')
        self.assertNotIn('secrets.', str(package_pipeline['jobs']['upload']))
        setup = yaml.safe_load((WORKFLOW.parents[1] / 'actions/setup-ppa-env/action.yml').read_text())
        cache = next(s for s in setup['runs']['steps'] if s.get('id') == 'pbuilder-cache')
        self.assertIn('${{ inputs.series }}', cache['with']['path'])
        self.assertEqual(cache['with']['restore-keys'].strip(), 'pbuilder-${{ inputs.series }}-')

    def test_rebuilt_revision_reaches_next_stage_despite_old_cache(self):
        jobs = yaml.safe_load(PIPELINE.read_text())['jobs']
        build = yaml.safe_load(STAGE_PIPELINE.read_text())['jobs']['build']
        cases = [(series, upstream, downstream)
                 for series in ['noble', 'resolute']
                 for upstream, downstream in [('stage1', 'stage2'),
                                              ('stage2', 'stage3'),
                                              ('stage3', 'stage4')]]
        for series, upstream, downstream in cases:
            with self.subTest(series=series, upstream=upstream), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cache, producer, consumer, artifacts = [
                    root / name for name in ('cache', 'producer', 'consumer', 'artifacts')
                ]
                for directory in (cache, producer, consumer, artifacts):
                    directory.mkdir()
                old = f'libimetable-dev_1.1.16-1~{series}1~ppa1_amd64.deb'
                new = f'libimetable-dev_1.1.16-1~{series}1~ppa2_amd64.deb'
                package = root / 'package'
                (package / 'DEBIAN').mkdir(parents=True)

                def build_deb(version, destination):
                    (package / 'DEBIAN/control').write_text(
                        f'Package: libimetable-dev\nVersion: {version}\n'
                        'Architecture: amd64\nMaintainer: Test <test@example.org>\n'
                        'Description: Handoff regression fixture\n'
                    )
                    subprocess.run(['dpkg-deb', '--build', str(package), str(destination)],
                                   check=True, capture_output=True)

                build_deb(f'1.1.16-1~{series}1~ppa1', cache / old)
                shutil.copytree(cache, producer, dirs_exist_ok=True)
                build_deb(f'1.1.16-1~{series}1~ppa2', producer / new)
                other = 'resolute' if series == 'noble' else 'noble'
                wrong_artifact = artifacts / f'debs-{other}-libime'
                wrong_artifact.mkdir()
                build_deb(f'1.1.16-1~{other}1~ppa99', wrong_artifact / 'other-series.deb')
                # Exact cache hits are immutable: the producer cannot save ppa2.
                self.assertTrue(jobs[downstream]['with']['upstream'])
                build_index = next(i for i, s in enumerate(build['steps'])
                                   if s.get('id') == 'build')
                for step in build['steps'][build_index + 1:]:
                    if step.get('name') == 'Upload debs for downstream stages':
                        self.assertEqual(step['uses'], 'actions/upload-artifact@v7')
                        self.assertEqual(step['with']['path'],
                                         '~/deb-cache/${{ matrix.package }}/*.deb')
                        self.assertEqual(step['with']['if-no-files-found'], 'error')
                        name = step['with']['name'].replace('${{ env.SERIES }}', series)
                        name = name.replace('${{ matrix.package }}', 'libime')
                        self.assertEqual(name, f'debs-{series}-libime')
                        shutil.copytree(producer, artifacts / name, dirs_exist_ok=True)
                for step in build['steps']:
                    if step.get('name') == 'Build local apt repo':
                        self.assertIn('"${{ runner.temp }}/upstream-debs"', step['run'])
                        break
                    if step.get('uses', '').startswith('actions/cache/restore@'):
                        shutil.copytree(cache, consumer, dirs_exist_ok=True)
                    if step.get('uses', '').startswith('actions/download-artifact@'):
                        self.assertEqual(step['uses'], 'actions/download-artifact@v8')
                        self.assertEqual(step['with']['pattern'], 'debs-${{ env.SERIES }}-*')
                        self.assertEqual(step['with']['path'],
                                         '${{ runner.temp }}/upstream-debs')
                        self.assertNotIn('run-id', step['with'])
                        self.assertTrue(list(artifacts.iterdir()))
                        pattern = step['with']['pattern'].replace('${{ env.SERIES }}', series)
                        for artifact in artifacts.iterdir():
                            if fnmatch.fnmatchcase(artifact.name, pattern):
                                shutil.copytree(artifact, consumer, dirs_exist_ok=True)
                self.assertTrue((consumer / new).exists(),
                                'downstream only received ppa1 from immutable cache')
                repo = root / 'repo'
                subprocess.run(
                    ['bash', str(WORKFLOW.parents[2] / 'scripts/build-local-repo.sh'),
                     str(consumer), str(repo)], check=True, capture_output=True,
                )
                self.assertIn(f'Version: 1.1.16-1~{series}1~ppa2\n',
                              (repo / 'Packages').read_text())
                self.assertNotIn(f'~{other}1~', (repo / 'Packages').read_text())


if __name__ == '__main__':
    unittest.main()
