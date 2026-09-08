import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/resolve-deb-version.sh"


class ResolveDebVersionTest(unittest.TestCase):
    def resolve(self, versions, apt_status=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = "".join(
                f"Package: fcitx5\nVersion: {version}\n\n" for version in versions
            )
            (root / "records").write_text(records)
            apt = root / "apt-cache"
            apt.write_text(
                '#!/bin/sh\ncat "$FIXTURE"\nexit "$APT_STATUS"\n'
            )
            apt.chmod(0o755)
            output = root / "output"
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}",
                       FIXTURE=str(root / "records"), APT_STATUS=str(apt_status),
                       GITHUB_OUTPUT=str(output))
            result = subprocess.run(
                ["bash", str(SCRIPT), "fcitx5"], env=env,
                capture_output=True, text=True,
            )
            return result, output.read_text() if output.exists() else ""

    def test_latest_version_regardless_of_record_order(self):
        for versions in (["5.1.21-1", "5.1.22-1"],
                         ["5.1.22-1", "5.1.21-1"],
                         ["5.1.22-1", "5.1.22-1"]):
            with self.subTest(versions=versions):
                result, output = self.resolve(versions)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, "deb_version=5.1.22-1\n")
                self.assertEqual(result.stdout, output)

    def test_debian_version_order(self):
        for versions, expected in [
            (["1.0-9", "1.0-10"], "1.0-10"),
            (["2.0~rc1-1", "2.0-1"], "2.0-1"),
            (["9.0-1", "1:1.0-1"], "1:1.0-1"),
        ]:
            with self.subTest(versions=versions):
                result, output = self.resolve(versions)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, f"deb_version={expected}\n")

    def test_large_output_is_fully_consumed(self):
        result, output = self.resolve(["5.1.21-1"] * 10000 + ["5.1.22-1"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, "deb_version=5.1.22-1\n")

    def test_missing_version_fails(self):
        result, output = self.resolve([])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot resolve deb version", result.stdout)
        self.assertEqual(output, "")

    def test_apt_failure_does_not_publish_partial_result(self):
        result, output = self.resolve(["5.1.22-1"], apt_status=100)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
