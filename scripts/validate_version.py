#!/usr/bin/env python3
"""Validate Groundhog's release version across repository metadata."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


VERSION_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
MAX_CHROME_COMPONENT = 65_535


class VersionError(ValueError):
    """Raised when release version metadata violates the contract."""


def validate_version_value(version: str) -> None:
    """Validate a version value against SemVer and Chrome's numeric limits."""
    if not VERSION_PATTERN.fullmatch(version):
        raise VersionError(
            f"VERSION must be stable MAJOR.MINOR.PATCH without leading zeros; got {version!r}"
        )

    components = tuple(int(component) for component in version.split("."))
    if not any(components):
        raise VersionError("VERSION cannot be 0.0.0 because Chrome rejects all-zero versions")
    if any(component > MAX_CHROME_COMPONENT for component in components):
        raise VersionError(
            f"VERSION components must be at most {MAX_CHROME_COMPONENT} for Chrome"
        )


def read_version(root: Path) -> str:
    version_path = root / "VERSION"
    try:
        raw_version = version_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VersionError(f"cannot read {version_path}: {exc}") from exc

    version = raw_version.rstrip("\n")
    if raw_version != f"{version}\n" or not version:
        raise VersionError("VERSION must contain only the version and one trailing newline")
    validate_version_value(version)
    return version


def read_manifest_version(root: Path) -> str:
    manifest_path = root / "extension" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionError(f"cannot read valid JSON from {manifest_path}: {exc}") from exc

    version = manifest.get("version")
    if not isinstance(version, str):
        raise VersionError("extension/manifest.json must contain a string version")
    return version


def validate(root: Path, tag: str | None = None) -> str:
    version = read_version(root)
    manifest_version = read_manifest_version(root)
    if manifest_version != version:
        raise VersionError(
            "version mismatch: "
            f"VERSION is {version!r}, extension/manifest.json is {manifest_version!r}"
        )

    if tag is not None:
        expected_tag = f"v{version}"
        if tag != expected_tag:
            raise VersionError(
                f"tag mismatch: expected {expected_tag!r} for VERSION {version!r}, got {tag!r}"
            )
    return version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check VERSION, the extension manifest, and an optional release tag."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--tag",
        help="release tag to check; it must be v followed by the VERSION value",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        version = validate(args.root.resolve(), args.tag)
    except VersionError as exc:
        print(f"version validation failed: {exc}", file=sys.stderr)
        return 1

    tag_message = f" and tag {args.tag}" if args.tag else ""
    print(f"version {version} is valid across VERSION and extension/manifest.json{tag_message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
