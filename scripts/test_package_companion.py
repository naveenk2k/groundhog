import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import package_companion


VERSION = "0.1.0"
ARCHIVE_NAME = package_companion.archive_name(VERSION)


def write_archive(path: Path, files: dict[str, bytes]) -> None:
    top_level = package_companion.top_level_name(VERSION)
    entries = [(top_level, True), (f"{top_level}/companion", True)]
    entries.extend((f"{top_level}/{name}", False) for name in files)
    with path.open("wb") as raw_output:
        with gzip.GzipFile(fileobj=raw_output, mode="wb", filename="", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as package:
                for name, directory in sorted(entries):
                    relative = name.removeprefix(f"{top_level}/")
                    payload = b"" if directory else files[relative]
                    info = package_companion._tar_info(
                        name, directory=directory, size=len(payload)
                    )
                    package.addfile(info, None if directory else io.BytesIO(payload))


def minimum_files() -> dict[str, bytes]:
    return {
        "LICENSE": b"license\n",
        "README.md": b"readme\n",
        "VERSION": b"0.1.0\n",
        "requirements.txt": b"fastapi\n",
        "companion/__init__.py": b"",
        "companion/app.py": b"app = object()\n",
    }


class PackageCompanionTests(unittest.TestCase):
    def test_build_is_deterministic_and_excludes_development_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            first_dir = Path(temporary_directory) / "first"
            second_dir = Path(temporary_directory) / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / ARCHIVE_NAME
            second = second_dir / ARCHIVE_NAME

            package_companion.build_archive(output=first)
            package_companion.build_archive(output=second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(package_companion.validate_archive(first), VERSION)
            with tarfile.open(first, "r:gz") as package:
                names = {member.name for member in package.getmembers()}
            self.assertFalse(any("test_" in name for name in names))
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any("extension" in name for name in names))

    def test_validation_rejects_wrong_archive_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / "wrong-name.tar.gz"
            write_archive(archive, minimum_files())

            with self.assertRaisesRegex(package_companion.PackageError, "archive filename"):
                package_companion.validate_archive(archive)

    def test_validation_rejects_missing_required_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / ARCHIVE_NAME
            files = minimum_files()
            del files["requirements.txt"]
            write_archive(archive, files)

            with self.assertRaisesRegex(package_companion.PackageError, "requirements.txt"):
                package_companion.validate_archive(archive)

    def test_validation_rejects_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / ARCHIVE_NAME
            files = minimum_files()
            files["companion/test_app.py"] = b""
            write_archive(archive, files)

            with self.assertRaisesRegex(package_companion.PackageError, "forbidden content"):
                package_companion.validate_archive(archive)

    def test_validation_rejects_local_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / ARCHIVE_NAME
            files = minimum_files()
            files["companion/corpus.sqlite3"] = b"private"
            write_archive(archive, files)

            with self.assertRaisesRegex(package_companion.PackageError, "forbidden content"):
                package_companion.validate_archive(archive)

    def test_validation_rejects_extension_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / ARCHIVE_NAME
            files = minimum_files()
            files["extension/manifest.json"] = b"{}"
            write_archive(archive, files)

            with self.assertRaisesRegex(package_companion.PackageError, "forbidden content"):
                package_companion.validate_archive(archive)

    def test_validation_rejects_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / ARCHIVE_NAME
            files = minimum_files()
            files["companion/private.key"] = b"secret"
            write_archive(archive, files)

            with self.assertRaisesRegex(package_companion.PackageError, "forbidden content"):
                package_companion.validate_archive(archive)


if __name__ == "__main__":
    unittest.main()
