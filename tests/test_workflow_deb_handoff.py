"""Replay the immutable-cache regression using the workflow's transfer steps.

Requires PyYAML (python3-yaml).
"""
from pathlib import Path
import fnmatch
import shutil
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/build-ppa.yml'


class DebHandoffTest(unittest.TestCase):
    def test_each_series_uses_its_own_runner_cache_and_four_stages(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        self.assertNotIn('SERIES', workflow['env'])
        self.assertEqual(workflow['concurrency']['group'], 'ppa-upload')
        expected = {
            'stage1': ['librime', 'yoga', 'fcitx5-gtk'],
            'stage2': ['fcitx5'],
            'stage3': ['libime', 'fcitx5-lua', 'fcitx5-qt', 'fcitx5-rime'],
            'stage4': ['fcitx5-chinese-addons'],
        }
        for index, (name, packages) in enumerate(expected.items(), start=1):
            job = workflow['jobs'][name]
            self.assertEqual(job.get('needs'), f'stage{index - 1}' if index > 1 else None)
            self.assertEqual(job['strategy']['max-parallel'], 1)
            self.assertFalse(job['strategy']['fail-fast'])
            matrix = job['strategy']['matrix']
            self.assertEqual(matrix['series'], ['noble', 'resolute'])
            self.assertEqual(matrix['package'], packages)
            self.assertEqual(matrix['include'], [
                {'series': 'noble', 'runner': 'ubuntu-24.04'},
                {'series': 'resolute', 'runner': 'ubuntu-26.04'},
            ])
            self.assertEqual(job['runs-on'], '${{ matrix.runner }}')
            self.assertEqual(job['env']['SERIES'], '${{ matrix.series }}')
            setup = next(s for s in job['steps']
                         if s.get('uses') == './.github/actions/setup-ppa-env')
            self.assertEqual(setup['with']['series'], '${{ env.SERIES }}')
            cache = next(s for s in job['steps']
                         if s.get('name') == 'Cache own debs')
            self.assertEqual(cache['with']['key'],
                             'debs-${{ matrix.package }}-${{ env.SERIES }}-'
                             '${{ steps.ver.outputs.deb_version }}')
        setup = yaml.safe_load((WORKFLOW.parents[1] / 'actions/setup-ppa-env/action.yml').read_text())
        cache = next(s for s in setup['runs']['steps'] if s.get('id') == 'pbuilder-cache')
        self.assertIn('${{ inputs.series }}', cache['with']['path'])
        self.assertEqual(cache['with']['restore-keys'].strip(), 'pbuilder-${{ inputs.series }}-')

    def test_rebuilt_revision_reaches_next_stage_despite_old_cache(self):
        jobs = yaml.safe_load(WORKFLOW.read_text())['jobs']
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
                build_index = next(i for i, s in enumerate(jobs[upstream]['steps'])
                                   if s.get('id') == 'build')
                for step in jobs[upstream]['steps'][build_index + 1:]:
                    if step.get('uses', '').startswith('actions/upload-artifact@'):
                        self.assertEqual(step['uses'], 'actions/upload-artifact@v7')
                        self.assertEqual(step['with']['path'],
                                         '~/deb-cache/${{ matrix.package }}/*.deb')
                        self.assertEqual(step['with']['if-no-files-found'], 'error')
                        name = step['with']['name'].replace('${{ env.SERIES }}', series)
                        name = name.replace('${{ matrix.package }}', 'libime')
                        self.assertEqual(name, f'debs-{series}-libime')
                        shutil.copytree(producer, artifacts / name, dirs_exist_ok=True)
                for step in jobs[downstream]['steps']:
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
