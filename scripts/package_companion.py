#!/usr/bin/env python3
"""Build and validate the Homebrew companion release archive."""

from __future__ import annotations

import argparse
import gzip
import io
import sys
import tarfile
from pathlib import Path, PurePosixPath

from validate_version import VersionError, read_version, validate_version_value


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = REPO_ROOT / "dist"
FIXED_MTIME = 0
REQUIRED_ROOT_FILES = ("LICENSE", "README.md", "VERSION", "requirements.txt")
REQUIRED_COMPANION_FILES = ("companion/__init__.py", "companion/app.py")
FORBIDDEN_DIRECTORY_NAMES = {
    ".cache",
    ".logs",
    ".venv",
    "__pycache__",
    "cache",
    "data",
    "extension",
    "logs",
    "tests",
    "venv",
}
FORBIDDEN_FILE_NAMES = {
    ".env",
    ".groundhog-secret",
    "corpus.db",
    "credentials.json",
    "watch-history.json",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pyc",
    ".sqlite",
    ".sqlite3",
}


class PackageError(ValueError):
    """Raised when a companion source tree or archive violates the contract."""


def archive_name(version: str) -> str:
    return f"groundhog-companion-{version}.tar.gz"


def top_level_name(version: str) -> str:
    return f"groundhog-companion-{version}"


def _is_forbidden(relative_path: PurePosixPath) -> bool:
    if any(part in FORBIDDEN_DIRECTORY_NAMES for part in relative_path.parts):
        return True
    if relative_path.name in FORBIDDEN_FILE_NAMES or relative_path.name.startswith(".env."):
        return True
    if relative_path.name.startswith("test_") or relative_path.name.endswith("_test.py"):
        return True
    return relative_path.suffix.lower() in FORBIDDEN_SUFFIXES


def source_files(root: Path = REPO_ROOT) -> dict[str, bytes]:
    """Read exactly the files allowed in the companion release."""
    files: dict[str, bytes] = {}
    for name in REQUIRED_ROOT_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise PackageError(f"required regular source file is missing: {name}")
        files[name] = path.read_bytes()

    companion_root = root / "companion"
    if not companion_root.is_dir() or companion_root.is_symlink():
        raise PackageError("required source directory is missing: companion")
    for path in companion_root.rglob("*"):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if _is_forbidden(relative):
            continue
        if path.is_symlink():
            raise PackageError(f"symbolic links are not allowed: {relative}")
        if path.is_file():
            files[relative.as_posix()] = path.read_bytes()

    for name in REQUIRED_COMPANION_FILES:
        if name not in files:
            raise PackageError(f"required companion source file is missing: {name}")
    return files


def _tar_info(name: str, *, directory: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(f"{name}/" if directory else name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.size = 0 if directory else size
    info.mode = 0o755 if directory else 0o644
    info.mtime = FIXED_MTIME
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


def _member_names(version: str, files: dict[str, bytes]) -> list[tuple[str, bool]]:
    prefix = top_level_name(version)
    directories = {prefix, f"{prefix}/companion"}
    for name in files:
        parent = PurePosixPath(name).parent
        while parent != PurePosixPath("."):
            directories.add(f"{prefix}/{parent.as_posix()}")
            parent = parent.parent
    entries = [(directory, True) for directory in directories]
    entries.extend((f"{prefix}/{name}", False) for name in files)
    return sorted(entries, key=lambda entry: entry[0])


def build_archive(root: Path = REPO_ROOT, output: Path | None = None) -> Path:
    try:
        version = read_version(root)
    except VersionError as exc:
        raise PackageError(str(exc)) from exc
    files = source_files(root)
    output = output or (DIST_ROOT / archive_name(version))
    if output.name != archive_name(version):
        raise PackageError(f"archive filename must be {archive_name(version)}")
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("wb") as raw_output:
        with gzip.GzipFile(fileobj=raw_output, mode="wb", filename="", mtime=FIXED_MTIME) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as package:
                prefix = f"{top_level_name(version)}/"
                for name, directory in _member_names(version, files):
                    relative_name = name.removeprefix(prefix)
                    payload = b"" if directory else files[relative_name]
                    info = _tar_info(name, directory=directory, size=len(payload))
                    package.addfile(info, None if directory else io.BytesIO(payload))

    validate_archive(output)
    return output


def _validate_member_metadata(member: tarfile.TarInfo) -> None:
    if not (member.isdir() or member.isreg()):
        raise PackageError(f"archive contains a link or special entry: {member.name}")
    expected_mode = 0o755 if member.isdir() else 0o644
    if member.mode != expected_mode:
        raise PackageError(f"archive member has non-normalized permissions: {member.name}")
    if member.mtime != FIXED_MTIME or member.uid != 0 or member.gid != 0:
        raise PackageError(f"archive member has non-normalized metadata: {member.name}")
    if member.uname != "root" or member.gname != "root":
        raise PackageError(f"archive member has non-normalized ownership: {member.name}")


def validate_archive(archive: Path) -> str:
    try:
        with archive.open("rb") as source:
            header = source.read(8)
        if len(header) < 8 or header[:2] != b"\x1f\x8b":
            raise PackageError("archive is not gzip-compressed")
        if int.from_bytes(header[4:8], "little") != FIXED_MTIME:
            raise PackageError("gzip header has a non-normalized timestamp")

        with tarfile.open(archive, mode="r:gz") as package:
            members = package.getmembers()
            names = [member.name.rstrip("/") for member in members]
            if names != sorted(names):
                raise PackageError("archive members are not sorted")
            if len(names) != len(set(names)):
                raise PackageError("archive contains duplicate member names")
            for member in members:
                _validate_member_metadata(member)

            top_levels: set[str] = set()
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise PackageError(f"archive contains an unsafe path: {name}")
                top_levels.add(path.parts[0])
            if len(top_levels) != 1:
                raise PackageError("archive must contain exactly one top-level directory")

            top_level = next(iter(top_levels))
            prefix = "groundhog-companion-"
            if not top_level.startswith(prefix):
                raise PackageError("top-level directory must be named groundhog-companion-VERSION")
            version = top_level.removeprefix(prefix)
            try:
                validate_version_value(version)
            except VersionError as exc:
                raise PackageError(str(exc)) from exc
            if archive.name != archive_name(version):
                raise PackageError(f"archive filename must be {archive_name(version)}")

            relative_files: dict[str, bytes] = {}
            for member in members:
                relative = PurePosixPath(member.name.rstrip("/")).relative_to(top_level)
                if relative == PurePosixPath("."):
                    continue
                if _is_forbidden(relative):
                    raise PackageError(f"archive contains forbidden content: {relative}")
                if relative.parts[0] not in {*REQUIRED_ROOT_FILES, "companion"}:
                    raise PackageError(f"archive contains unexpected content: {relative}")
                if member.isreg():
                    extracted = package.extractfile(member)
                    if extracted is None:
                        raise PackageError(f"cannot read archive member: {relative}")
                    relative_files[relative.as_posix()] = extracted.read()
    except (OSError, tarfile.TarError) as exc:
        raise PackageError(f"cannot read companion archive: {exc}") from exc

    for name in (*REQUIRED_ROOT_FILES, *REQUIRED_COMPANION_FILES):
        if name not in relative_files:
            raise PackageError(f"required archive file is missing: {name}")
    try:
        archived_version = relative_files["VERSION"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError("archived VERSION is not UTF-8") from exc
    if archived_version != f"{version}\n":
        raise PackageError("archive filename, directory, and VERSION do not match")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", type=Path, help="validate an existing tar.gz instead of building one"
    )
    parser.add_argument("--output", type=Path, help="write the tar.gz to this path")
    args = parser.parse_args(argv)
    try:
        if args.check:
            if args.output:
                parser.error("--output cannot be used with --check")
            version = validate_archive(args.check)
            print(f"Valid companion package: {args.check} (version {version})")
        else:
            output = build_archive(output=args.output)
            print(f"Built and validated companion package: {output}")
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
