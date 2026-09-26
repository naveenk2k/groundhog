import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("validate_version.py")


class ValidateVersionTest(unittest.TestCase):
    def run_validator(
        self, version: str, manifest_version: str, tag: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "extension").mkdir()
            (root / "VERSION").write_text(version, encoding="utf-8")
            (root / "extension" / "manifest.json").write_text(
                json.dumps({"version": manifest_version}), encoding="utf-8"
            )
            command = [sys.executable, str(SCRIPT), "--root", str(root)]
            if tag is not None:
                command.extend(("--tag", tag))
            return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_accepts_matching_stable_version_and_tag(self) -> None:
        result = self.run_validator("0.1.0\n", "0.1.0", "v0.1.0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version 0.1.0 is valid", result.stdout)

    def test_rejects_manifest_mismatch(self) -> None:
        result = self.run_validator("0.1.0\n", "0.2.0")

        self.assertEqual(result.returncode, 1)
        self.assertIn("version mismatch", result.stderr)

    def test_rejects_tag_mismatch(self) -> None:
        result = self.run_validator("0.1.0\n", "0.1.0", "v0.1.1")

        self.assertEqual(result.returncode, 1)
        self.assertIn("tag mismatch", result.stderr)

    def test_rejects_prerelease_version(self) -> None:
        result = self.run_validator("0.2.0-beta.1\n", "0.2.0-beta.1")

        self.assertEqual(result.returncode, 1)
        self.assertIn("stable MAJOR.MINOR.PATCH", result.stderr)

    def test_rejects_missing_trailing_newline(self) -> None:
        result = self.run_validator("0.1.0", "0.1.0")

        self.assertEqual(result.returncode, 1)
        self.assertIn("one trailing newline", result.stderr)

    def test_rejects_version_too_large_for_chrome(self) -> None:
        result = self.run_validator("0.65536.0\n", "0.65536.0")

        self.assertEqual(result.returncode, 1)
        self.assertIn("at most 65535", result.stderr)

    def test_rejects_all_zero_version(self) -> None:
        result = self.run_validator("0.0.0\n", "0.0.0")

        self.assertEqual(result.returncode, 1)
        self.assertIn("all-zero", result.stderr)


if __name__ == "__main__":
    unittest.main()
