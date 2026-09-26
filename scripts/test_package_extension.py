import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import package_extension


class PackageExtensionTests(unittest.TestCase):
    def test_build_is_deterministic_and_contains_only_runtime_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            first = Path(temporary_directory) / "first.zip"
            second = Path(temporary_directory) / "second.zip"
            package_extension.build_archive(output=first)
            package_extension.build_archive(output=second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            files = package_extension.read_archive(first)
            self.assertIn("manifest.json", files)
            self.assertNotIn("package.json", files)
            self.assertFalse(any(name.endswith(".test.js") for name in files))
            self.assertFalse(any("secret" in name.lower() for name in files))
            self.assertEqual(set(files), package_extension.runtime_files(files))

    def test_validation_rejects_archive_with_top_level_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / "nested.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("groundhog/manifest.json", "{}")

            with self.assertRaisesRegex(package_extension.PackageError, "archive root"):
                package_extension.validate_archive(archive)

    def test_validation_rejects_development_file(self):
        files = package_extension.read_source_tree()
        runtime = package_extension.runtime_files(files)
        packaged = {name: files[name] for name in runtime}
        packaged["content.test.js"] = b""

        with self.assertRaisesRegex(package_extension.PackageError, "non-runtime file"):
            package_extension.validate_files(packaged)

    def test_validation_rejects_invalid_chrome_version(self):
        files = package_extension.read_source_tree()
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = "1.02.3"
        files["manifest.json"] = json.dumps(manifest).encode()

        with self.assertRaisesRegex(package_extension.PackageError, "manifest version"):
            package_extension.runtime_files(files)


if __name__ == "__main__":
    unittest.main()
