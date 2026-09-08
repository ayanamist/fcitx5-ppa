"""Replay the immutable-cache regression using the workflow's transfer steps.

Requires PyYAML (python3-yaml).
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/build-ppa.yml'


class DebHandoffTest(unittest.TestCase):
    def test_rebuilt_revision_reaches_next_stage_despite_old_cache(self):
        jobs = yaml.safe_load(WORKFLOW.read_text())['jobs']
        for upstream, downstream in [('stage1', 'stage2'),
                                     ('stage2', 'stage3'),
                                     ('stage3', 'stage4')]:
            with self.subTest(upstream=upstream), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cache, producer, consumer, artifacts = [
                    root / name for name in ('cache', 'producer', 'consumer', 'artifacts')
                ]
                for directory in (cache, producer, consumer, artifacts):
                    directory.mkdir()
                old = 'libimetable-dev_1.1.16-1~noble1~ppa1_amd64.deb'
                new = 'libimetable-dev_1.1.16-1~noble1~ppa2_amd64.deb'
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

                build_deb('1.1.16-1~noble1~ppa1', cache / old)
                shutil.copytree(cache, producer, dirs_exist_ok=True)
                build_deb('1.1.16-1~noble1~ppa2', producer / new)
                # Exact cache hits are immutable: the producer cannot save ppa2.
                build_index = next(i for i, s in enumerate(jobs[upstream]['steps'])
                                   if s.get('id') == 'build')
                for step in jobs[upstream]['steps'][build_index + 1:]:
                    if step.get('uses', '').startswith('actions/upload-artifact@'):
                        self.assertEqual(step['with']['path'],
                                         '~/deb-cache/${{ matrix.package }}/*.deb')
                        self.assertEqual(step['with']['if-no-files-found'], 'error')
                        shutil.copytree(producer, artifacts, dirs_exist_ok=True)
                for step in jobs[downstream]['steps']:
                    if step.get('name') == 'Build local apt repo':
                        self.assertIn('"${{ runner.temp }}/upstream-debs"', step['run'])
                        break
                    if step.get('uses', '').startswith('actions/cache/restore@'):
                        shutil.copytree(cache, consumer, dirs_exist_ok=True)
                    if step.get('uses', '').startswith('actions/download-artifact@'):
                        self.assertEqual(step['with']['pattern'], 'debs-*')
                        self.assertEqual(step['with']['path'],
                                         '${{ runner.temp }}/upstream-debs')
                        self.assertNotIn('run-id', step['with'])
                        self.assertTrue(list(artifacts.iterdir()))
                        shutil.copytree(artifacts, consumer, dirs_exist_ok=True)
                self.assertTrue((consumer / new).exists(),
                                'downstream only received ppa1 from immutable cache')
                repo = root / 'repo'
                subprocess.run(
                    ['bash', str(WORKFLOW.parents[2] / 'scripts/build-local-repo.sh'),
                     str(consumer), str(repo)], check=True, capture_output=True,
                )
                self.assertIn('Version: 1.1.16-1~noble1~ppa2\n',
                              (repo / 'Packages').read_text())


if __name__ == '__main__':
    unittest.main()
