#!/usr/bin/env python3
"""Generate the temporary Codex legacy manifest from Agent Plugins metadata.

Root ``plugin.json`` and ``extensions.com.openai`` are authoritative. The
generated ``.codex-plugin/plugin.json`` exists only for Codex versions that
predate native Agent Plugins 1.0 support.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any


AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PORTABLE_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
PORTABLE_METADATA_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
# The catalog currently publishes skill-only packages. Apps and hooks are valid
# Codex extension concepts, but require their own schemas and security review.
CODEX_EXTENSION_FIELDS = {"interface"}


class OverlayError(ValueError):
    """Raised when a package cannot produce a safe deterministic overlay."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OverlayError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise OverlayError(f"non-JSON numeric constant: {value}")


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_json_constant,
        )
    except OSError as exc:
        raise OverlayError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OverlayError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OverlayError(f"{path} must contain a JSON object")
    return value


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def build_legacy_overlay(package_root: Path) -> dict[str, Any]:
    package_root = Path(os.path.abspath(package_root.expanduser()))
    if package_root.is_symlink() or not package_root.is_dir():
        raise OverlayError(f"package root must be a real directory: {package_root}")
    manifest_path = package_root / "plugin.json"
    if manifest_path.is_symlink():
        raise OverlayError(f"portable manifest must not be a symlink: {manifest_path}")
    manifest = load_json_object(manifest_path)

    unknown = sorted(set(manifest) - PORTABLE_FIELDS)
    if unknown:
        raise OverlayError(
            f"{manifest_path} has non-portable field(s): {', '.join(unknown)}"
        )
    if manifest.get("$schema") != AGENT_PLUGIN_SCHEMA:
        raise OverlayError(
            f"{manifest_path} must target Agent Plugins 1.0.0 ({AGENT_PLUGIN_SCHEMA})"
        )

    for field in ("name", "version", "description"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            raise OverlayError(f"{manifest_path} field {field!r} must be non-empty")
    author = manifest.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str):
        raise OverlayError(f"{manifest_path} field 'author.name' must be present")

    extensions = manifest.get("extensions")
    if not isinstance(extensions, dict):
        raise OverlayError(f"{manifest_path} field 'extensions' must be an object")
    codex_extension = extensions.get("com.openai")
    if not isinstance(codex_extension, dict):
        raise OverlayError(
            f"{manifest_path} must define authoritative extensions.com.openai metadata"
        )
    unknown_extension_fields = sorted(set(codex_extension) - CODEX_EXTENSION_FIELDS)
    if unknown_extension_fields:
        raise OverlayError(
            "extensions.com.openai contains unsupported bridge field(s): "
            + ", ".join(unknown_extension_fields)
        )
    if not isinstance(codex_extension.get("interface"), dict):
        raise OverlayError("extensions.com.openai.interface must be an object")

    skills_root = package_root / "skills"
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise OverlayError(f"{package_root} must contain a real skills directory")
    if (package_root / "mcp.json").exists():
        raise OverlayError(
            "the legacy bridge is skill-only; mcp.json requires an explicit translator"
        )
    if (package_root / ".mcp.json").exists():
        raise OverlayError(
            "legacy .mcp.json must not be authored beside the portable source"
        )

    overlay: dict[str, Any] = {}
    for field in PORTABLE_METADATA_FIELDS:
        if field in manifest:
            overlay[field] = manifest[field]
    overlay["skills"] = "./skills/"
    for field in ("interface",):
        if field in codex_extension:
            overlay[field] = codex_extension[field]
    return overlay


def overlay_path(package_root: Path) -> Path:
    return package_root / ".codex-plugin" / "plugin.json"


def check_overlay(package_root: Path) -> list[str]:
    expected = canonical_json(build_legacy_overlay(package_root))
    path = overlay_path(package_root)
    if path.is_symlink() or path.parent.is_symlink():
        return [f"generated overlay must not be a symlink: {path}"]
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"missing generated overlay: {path}"]
    except OSError as exc:
        return [f"cannot read generated overlay {path}: {exc}"]
    if actual == expected:
        return []
    diff = "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (generated)",
        )
    )
    return [f"generated overlay is stale: {path}\n{diff.rstrip()}"]


def write_overlay(package_root: Path) -> Path:
    contents = canonical_json(build_legacy_overlay(package_root))
    path = overlay_path(package_root)
    if path.is_symlink() or path.parent.is_symlink():
        raise OverlayError(f"generated overlay must not be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="fail when an overlay differs")
    action.add_argument("--write", action="store_true", help="write generated overlays")
    parser.add_argument("package_roots", nargs="+", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    failures: list[str] = []
    for package_root in args.package_roots:
        try:
            if args.check:
                failures.extend(check_overlay(package_root))
            else:
                print(write_overlay(package_root))
        except OverlayError as exc:
            failures.append(str(exc))
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    if args.check:
        print(f"Validated {len(args.package_roots)} generated legacy overlay(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
