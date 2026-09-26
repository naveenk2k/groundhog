#!/usr/bin/env python3
"""Build and validate the Chrome Web Store upload archive."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = REPO_ROOT / "extension"
DIST_ROOT = EXTENSION_ROOT / "dist"
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
CHROME_VERSION_RE = re.compile(r"^(?:0|[1-9]\d{0,4})(?:\.(?:0|[1-9]\d{0,4})){0,3}$")
JS_RUNTIME_REFERENCE_RE = re.compile(
    r"(?:importScripts|chrome\.runtime\.getURL)\(\s*[\"']([^\"']+)[\"']"
)
HTML_RUNTIME_REFERENCE_RE = re.compile(r"(?:src|href)=[\"']([^\"']+)[\"']", re.IGNORECASE)


class PackageError(ValueError):
    """Raised when an extension source tree or archive is not store-ready."""


def _normalise_member(reference: str, parent: PurePosixPath = PurePosixPath()) -> str | None:
    reference = reference.split("?", 1)[0].split("#", 1)[0]
    if not reference or reference.startswith(("http://", "https://", "data:", "#")):
        return None
    candidate = parent / reference
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PackageError(f"unsafe runtime path: {reference}")
    return candidate.as_posix()


def _manifest_references(manifest: Mapping[str, object]) -> set[str]:
    references: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str):
            member = _normalise_member(value)
            if member:
                references.add(member)

    background = manifest.get("background", {})
    if isinstance(background, dict):
        add(background.get("service_worker"))
        scripts = background.get("scripts", [])
        if isinstance(scripts, list):
            for script in scripts:
                add(script)

    for content_script in manifest.get("content_scripts", []):
        if not isinstance(content_script, dict):
            continue
        for key in ("js", "css"):
            values = content_script.get(key, [])
            if isinstance(values, list):
                for value in values:
                    add(value)

    for key in ("icons",):
        values = manifest.get(key, {})
        if isinstance(values, dict):
            for value in values.values():
                add(value)

    action = manifest.get("action", {})
    if isinstance(action, dict):
        add(action.get("default_popup"))
        default_icon = action.get("default_icon", {})
        if isinstance(default_icon, dict):
            for value in default_icon.values():
                add(value)
        else:
            add(default_icon)

    options_ui = manifest.get("options_ui", {})
    if isinstance(options_ui, dict):
        add(options_ui.get("page"))
    add(manifest.get("options_page"))

    for resource_group in manifest.get("web_accessible_resources", []):
        if not isinstance(resource_group, dict):
            continue
        resources = resource_group.get("resources", [])
        if isinstance(resources, list):
            for resource in resources:
                add(resource)

    return references


def _load_manifest(files: Mapping[str, bytes]) -> dict[str, object]:
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
    except KeyError as exc:
        raise PackageError("manifest.json must be at the archive root") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"manifest.json is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PackageError("manifest.json must contain a JSON object")
    if manifest.get("manifest_version") != 3:
        raise PackageError("manifest_version must be 3")
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        raise PackageError("manifest name must be a non-empty string")
    version = manifest.get("version")
    if not isinstance(version, str) or not CHROME_VERSION_RE.fullmatch(version):
        raise PackageError("manifest version must contain one to four dot-separated integers")
    if any(int(component) > 65535 for component in version.split(".")):
        raise PackageError("manifest version components must be no greater than 65535")
    if all(int(component) == 0 for component in version.split(".")):
        raise PackageError("manifest version cannot be all zeroes")
    return manifest


def runtime_files(files: Mapping[str, bytes]) -> set[str]:
    """Return the transitive set of files referenced by the extension manifest."""
    manifest = _load_manifest(files)
    required = {"manifest.json", *_manifest_references(manifest)}
    pending = list(required - {"manifest.json"})
    scanned: set[str] = set()

    while pending:
        member = pending.pop()
        if member in scanned:
            continue
        scanned.add(member)
        if member not in files:
            raise PackageError(f"required runtime file is missing: {member}")

        suffix = PurePosixPath(member).suffix.lower()
        if suffix not in {".js", ".html"}:
            continue
        try:
            text = files[member].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PackageError(f"runtime source is not UTF-8: {member}") from exc

        pattern = JS_RUNTIME_REFERENCE_RE if suffix == ".js" else HTML_RUNTIME_REFERENCE_RE
        for reference in pattern.findall(text):
            child = _normalise_member(reference, PurePosixPath(member).parent)
            if child and child not in required:
                required.add(child)
                pending.append(child)

    missing = required - files.keys()
    if missing:
        raise PackageError(f"required runtime file is missing: {sorted(missing)[0]}")
    return required


def read_source_tree(extension_root: Path = EXTENSION_ROOT) -> dict[str, bytes]:
    return {
        path.relative_to(extension_root).as_posix(): path.read_bytes()
        for path in extension_root.rglob("*")
        if path.is_file() and "dist" not in path.relative_to(extension_root).parts
    }


def validate_files(files: Mapping[str, bytes]) -> dict[str, object]:
    manifest = _load_manifest(files)
    expected = runtime_files(files)
    actual = set(files)
    unexpected = sorted(actual - expected)
    if unexpected:
        raise PackageError(f"archive contains non-runtime file: {unexpected[0]}")
    return manifest


def read_archive(archive: Path) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(archive) as package:
            names = package.namelist()
            if len(names) != len(set(names)):
                raise PackageError("archive contains duplicate file names")
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or name.endswith("/"):
                    raise PackageError(f"archive contains an unsafe or unnecessary entry: {name}")
            return {name: package.read(name) for name in names}
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError(f"cannot read ZIP archive: {exc}") from exc


def validate_archive(archive: Path) -> dict[str, object]:
    return validate_files(read_archive(archive))


def build_archive(extension_root: Path = EXTENSION_ROOT, output: Path | None = None) -> Path:
    source_files = read_source_tree(extension_root)
    manifest = _load_manifest(source_files)
    members = runtime_files(source_files)
    version = str(manifest["version"])
    output = output or (DIST_ROOT / f"groundhog-extension-{version}.zip")
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as package:
        for member in sorted(members):
            info = zipfile.ZipInfo(member, FIXED_ZIP_TIME)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            package.writestr(info, source_files[member])

    validate_archive(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path, help="validate an existing ZIP instead of building one")
    parser.add_argument("--output", type=Path, help="write the ZIP to this path")
    args = parser.parse_args(argv)
    try:
        if args.check:
            if args.output:
                parser.error("--output cannot be used with --check")
            manifest = validate_archive(args.check)
            print(f"Valid Chrome package: {args.check} (version {manifest['version']})")
        else:
            output = build_archive(output=args.output)
            print(f"Built and validated Chrome package: {output}")
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
