#!/usr/bin/env python3
"""Validate the Codex marketplace and Agent Plugins packages without running them."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import urlparse

from generate_legacy_overlay import AGENT_PLUGIN_SCHEMA
from generate_legacy_overlay import CODEX_EXTENSION_FIELDS
from generate_legacy_overlay import OverlayError
from generate_legacy_overlay import check_overlay
from generate_legacy_overlay import load_json_object
from monitor_lib import MonitorError as DriftLockError
from monitor_lib import load_json as load_drift_lock
from monitor_lib import validate_release_lock_structure


MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKETPLACE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\."
    r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
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
MARKETPLACE_ENTRY_FIELDS = {
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "interface",
    "source",
    "policy",
    "category",
}
MARKETPLACE_METADATA_FIELDS = (
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
INTERFACE_FIELDS = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
    "capabilities",
    "websiteURL",
    "privacyPolicyURL",
    "termsOfServiceURL",
    "defaultPrompt",
    "brandColor",
    "composerIcon",
    "logo",
    "logoDark",
    "screenshots",
}
REQUIRED_INTERFACE_FIELDS = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
    "capabilities",
    "defaultPrompt",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
)
GIT_LFS_POINTER_RE = re.compile(
    rb"\Aversion https://git-lfs\.github\.com/spec/v1\r?\n"
    rb"oid sha256:[0-9a-f]{64}\r?\n"
    rb"size [0-9]+(?:\r?\n|\Z)"
)
MAX_PACKAGE_FILES = 5_000
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
AGENT_PLUGINS_RELEASE_LOCK = {
    "version": "1.0.0",
    "status": "published",
    "repository": "https://github.com/agentplugins/agent-plugins-spec",
    "default_branch": "main",
    "upstream_path": "spec",
    "commit": "bd383552095128f6effe895b9257cfd580a6d179",
    "release_discovery": {
        "strategy": "semver-files",
        "path": "spec",
        "suffix": ".md",
    },
    "specification": {
        "path": "spec/1.0.0.md",
        "vendored_path": "vendor/agent-plugins-spec/1.0.0/specification.md",
        "sha256": "97a658b7dca3ce1b4c2266b95da300fa51d9dc4ade59d73168e5f9104272da18",
    },
    "specification_license": {
        "spdx": "CC-BY-4.0",
        "path": "LICENSES/CC-BY-4.0.txt",
        "vendored_path": "vendor/agent-plugins-spec/LICENSES/CC-BY-4.0.txt",
        "sha256": "9e5f1b3c610b9c2da5c313bf81d577a7d1acec686bdb0384edefa6df0f90cd94",
    },
    "schemas_license": {
        "spdx": "Apache-2.0",
        "path": "LICENSES/Apache-2.0.txt",
        "vendored_path": "vendor/agent-plugins-spec/LICENSES/Apache-2.0.txt",
        "sha256": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    },
}
AGENT_PLUGIN_SCHEMA_LOCKS = {
    "plugin": {
        "id": AGENT_PLUGIN_SCHEMA,
        "upstream_path": "schemas/1.0.0/plugin.schema.json",
        "path": "vendor/agent-plugins-spec/1.0.0/plugin.schema.json",
        "sha256": "0a4aad95ce337878ad38802ebf0daa3fde76abe3f65400c86bcbb1ec0b3ab883",
    },
    "mcp": {
        "id": MCP_SCHEMA,
        "upstream_path": "schemas/1.0.0/mcp.schema.json",
        "path": "vendor/agent-plugins-spec/1.0.0/mcp.schema.json",
        "sha256": "6539175bfcdf43085855183e86da40ea94b166547a72b47ae9a0a390516d3acb",
    },
}
AGENT_SKILLS_LOCK = {
    "status": "unversioned",
    "repository": "https://github.com/agentskills/agentskills",
    "default_branch": "main",
    "upstream_path": "docs/specification.mdx",
    "commit": "217be548739f21d6008915c29aefe320ea1a90af",
    "release_discovery": {
        "strategy": "unversioned-file",
        "path": "docs/specification.mdx",
    },
    "specification": {
        "path": "docs/specification.mdx",
        "vendored_path": "vendor/agent-skills/unversioned/specification.mdx",
        "sha256": "b9079c0c10b7930e8c6a20ff2bc10cda2a3343c55185120e3f1116a1a529b220",
    },
    "license": {
        "spdx": "CC-BY-4.0",
        "path": "docs/LICENSE",
        "vendored_path": "vendor/agent-skills/LICENSES/CC-BY-4.0.txt",
        "sha256": "9e5f1b3c610b9c2da5c313bf81d577a7d1acec686bdb0384edefa6df0f90cd94",
    },
    "catalog_frontmatter_profile": {
        "allowed_fields": ["name", "description"],
        "required_fields": ["name", "description"],
        "name_format": "lowercase-hyphen-case",
        "name_max_length": 64,
        "description_max_length": 1024,
        "description_forbidden_characters": ["<", ">"],
    },
}
SKILL_FRONTMATTER_FIELDS = frozenset(
    AGENT_SKILLS_LOCK["catalog_frontmatter_profile"]["allowed_fields"]
)
SKILL_FRONTMATTER_REQUIRED = frozenset(
    AGENT_SKILLS_LOCK["catalog_frontmatter_profile"]["required_fields"]
)
SKILL_NAME_MAX_LENGTH = AGENT_SKILLS_LOCK["catalog_frontmatter_profile"][
    "name_max_length"
]
SKILL_DESCRIPTION_MAX_LENGTH = AGENT_SKILLS_LOCK["catalog_frontmatter_profile"][
    "description_max_length"
]
SKILL_DESCRIPTION_FORBIDDEN = tuple(
    AGENT_SKILLS_LOCK["catalog_frontmatter_profile"]["description_forbidden_characters"]
)
CODEX_COMPATIBILITY_LOCK = {
    "agent_plugins_minimum_version": "0.146.0",
    "legacy_overlay_target": "0.145.x and earlier",
}


class CatalogError(ValueError):
    """Raised for a package-source error that prevents further validation."""


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _resolve_local_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise CatalogError(f"unsupported non-local schema reference: {ref}")
    value: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise CatalogError(f"unresolvable schema reference: {ref}")
        value = value[part]
    if not isinstance(value, dict):
        raise CatalogError(f"schema reference does not resolve to an object: {ref}")
    return value


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str = "$",
    root_schema: dict[str, Any] | None = None,
) -> list[str]:
    """Validate the JSON Schema subset used by the vendored 1.0.0 schemas."""

    root_schema = schema if root_schema is None else root_schema
    if "$ref" in schema:
        return validate_json_schema(
            value,
            _resolve_local_ref(root_schema, schema["$ref"]),
            path=path,
            root_schema=root_schema,
        )
    if "oneOf" in schema:
        variants = [
            validate_json_schema(value, item, path=path, root_schema=root_schema)
            for item in schema["oneOf"]
        ]
        if sum(not errors for errors in variants) != 1:
            return [f"{path} must match exactly one allowed schema variant"]
        return []
    if "not" in schema and not validate_json_schema(
        value,
        schema["not"],
        path=path,
        root_schema=root_schema,
    ):
        return [f"{path} matches a forbidden schema"]

    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']!r}")

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _type_matches(value, expected_type):
        return [f"{path} must be a JSON {expected_type}"]

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path} is shorter than {schema['minLength']} character(s)")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path} is longer than {schema['maxLength']} character(s)")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path} does not match {schema['pattern']!r}")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(
                validate_json_schema(
                    item,
                    schema["items"],
                    path=f"{path}[{index}]",
                    root_schema=root_schema,
                )
            )

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path} is missing required field {required!r}")
        for key, item in value.items():
            if key in properties:
                errors.extend(
                    validate_json_schema(
                        item,
                        properties[key],
                        path=f"{path}.{key}",
                        root_schema=root_schema,
                    )
                )
            else:
                additional = schema.get("additionalProperties", True)
                if additional is False:
                    errors.append(f"{path} has unknown field {key!r}")
                elif isinstance(additional, dict):
                    errors.extend(
                        validate_json_schema(
                            item,
                            additional,
                            path=f"{path}.{key}",
                            root_schema=root_schema,
                        )
                    )
        property_names = schema.get("propertyNames")
        if isinstance(property_names, dict):
            for key in value:
                errors.extend(
                    validate_json_schema(
                        key,
                        property_names,
                        path=f"{path} key {key!r}",
                        root_schema=root_schema,
                    )
                )
    return errors


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_standard_lock(repo_root: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    lock_path = repo_root / "standards.lock.json"
    try:
        lock = load_json_object(lock_path)
    except (OverlayError, OSError) as exc:
        return [str(exc)], {}
    if set(lock) != {"format_version", "agent_plugins", "agent_skills", "codex"}:
        errors.append("standards.lock.json has unexpected or missing top-level fields")
    if lock.get("format_version") != 1:
        errors.append("standards.lock.json format_version must be 1")

    agent_plugins = lock.get("agent_plugins")
    if not isinstance(agent_plugins, dict):
        return ["standards.lock.json must define agent_plugins"], {}
    release_metadata = {
        key: value for key, value in agent_plugins.items() if key != "schemas"
    }
    if release_metadata != AGENT_PLUGINS_RELEASE_LOCK:
        errors.append("standards.lock.json must retain the reviewed Agent Plugins release")
    for document_name in ("specification", "specification_license", "schemas_license"):
        document_lock = AGENT_PLUGINS_RELEASE_LOCK[document_name]
        relative = document_lock["vendored_path"]
        candidate = repo_root / relative
        path = candidate.resolve()
        if not path.is_relative_to(repo_root.resolve()):
            errors.append(
                f"the vendored Agent Plugins {document_name} path escapes the repository"
            )
            continue
        if candidate.is_symlink() or not candidate.is_file():
            errors.append(
                f"the vendored Agent Plugins {document_name} is missing: {relative}"
            )
            continue
        actual_hash = sha256_file(candidate)
        if actual_hash != document_lock["sha256"]:
            errors.append(
                f"the vendored Agent Plugins {document_name} hash is {actual_hash}, "
                f"not {document_lock['sha256']}"
            )

    agent_skills = lock.get("agent_skills")
    if agent_skills != AGENT_SKILLS_LOCK:
        errors.append(
            "standards.lock.json must retain the reviewed Agent Skills dependency "
            "and catalog frontmatter profile"
        )
    for document_name in ("specification", "license"):
        document_lock = AGENT_SKILLS_LOCK[document_name]
        relative = document_lock["vendored_path"]
        candidate = repo_root / relative
        path = candidate.resolve()
        if not path.is_relative_to(repo_root.resolve()):
            errors.append(f"the vendored Agent Skills {document_name} path escapes the repository")
            continue
        if candidate.is_symlink() or not candidate.is_file():
            errors.append(f"the vendored Agent Skills {document_name} is missing: {relative}")
            continue
        actual_hash = sha256_file(candidate)
        if actual_hash != document_lock["sha256"]:
            errors.append(
                f"the vendored Agent Skills {document_name} hash is {actual_hash}, "
                f"not {document_lock['sha256']}"
            )
    if lock.get("codex") != CODEX_COMPATIBILITY_LOCK:
        errors.append("standards.lock.json must retain the reviewed Codex compatibility range")

    schemas: dict[str, dict[str, Any]] = {}
    schema_locks = agent_plugins.get("schemas")
    if not isinstance(schema_locks, dict):
        return errors + ["standards.lock.json must define agent_plugins.schemas"], {}
    for name, expected_lock in AGENT_PLUGIN_SCHEMA_LOCKS.items():
        entry = schema_locks.get(name)
        if not isinstance(entry, dict):
            errors.append(f"standards.lock.json is missing the {name} schema lock")
            continue
        if entry != expected_lock:
            errors.append(f"the locked {name} schema metadata is not the reviewed version")
        relative = entry.get("path")
        if not isinstance(relative, str):
            errors.append(f"the locked {name} schema path must be a string")
            continue
        path = (repo_root / relative).resolve()
        if not path.is_relative_to(repo_root.resolve()):
            errors.append(f"the locked {name} schema path escapes the repository")
            continue
        if not path.is_file():
            errors.append(f"the locked {name} schema is missing: {relative}")
            continue
        actual_hash = sha256_file(path)
        if actual_hash != entry.get("sha256"):
            errors.append(
                f"the vendored {name} schema hash is {actual_hash}, not {entry.get('sha256')}"
            )
        try:
            schema = load_json_object(path)
        except OverlayError as exc:
            errors.append(str(exc))
            continue
        if schema.get("$id") != expected_lock["id"]:
            errors.append(f"the vendored {name} schema has the wrong $id")
        schemas[name] = schema
    return errors, schemas


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


def _validate_relative_file(
    package_root: Path,
    raw_path: Any,
    label: str,
) -> list[str]:
    if not isinstance(raw_path, str) or not raw_path.startswith("./"):
        return [f"{label} must be a ./-prefixed relative path"]
    relative = PurePosixPath(raw_path[2:])
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        return [f"{label} must stay inside the plugin root"]
    resolved = (package_root / Path(*relative.parts)).resolve()
    if not resolved.is_relative_to(package_root.resolve()):
        return [f"{label} escapes the plugin root"]
    if not resolved.is_file():
        return [f"{label} points to a missing file: {raw_path}"]
    return []


def validate_openai_interface(
    package_root: Path,
    manifest: dict[str, Any],
    interface: Any,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(interface, dict):
        return ["extensions.com.openai.interface must be an object"]
    unknown = sorted(set(interface) - INTERFACE_FIELDS)
    if unknown:
        errors.append("extensions.com.openai.interface has unknown field(s): " + ", ".join(unknown))
    missing = sorted(REQUIRED_INTERFACE_FIELDS - set(interface))
    if missing:
        errors.append("extensions.com.openai.interface is missing: " + ", ".join(missing))
    for field in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
    ):
        if field in interface and not _non_empty_string(interface[field]):
            errors.append(f"extensions.com.openai.interface.{field} must be non-empty")
    capabilities = interface.get("capabilities")
    if capabilities is not None and (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(_non_empty_string(item) for item in capabilities)
    ):
        errors.append("extensions.com.openai.interface.capabilities must be a non-empty string array")
    prompts = interface.get("defaultPrompt")
    if isinstance(prompts, str):
        prompts = [prompts]
    if prompts is not None and (
        not isinstance(prompts, list)
        or not 1 <= len(prompts) <= 3
        or not all(_non_empty_string(item) and len(item) <= 128 for item in prompts)
    ):
        errors.append("extensions.com.openai.interface.defaultPrompt must contain 1-3 prompts of at most 128 characters")
    for field in ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
        if field in interface and not _is_https_url(interface[field]):
            errors.append(f"extensions.com.openai.interface.{field} must be an HTTPS URL")
    if "brandColor" in interface and (
        not isinstance(interface["brandColor"], str)
        or HEX_COLOR_RE.fullmatch(interface["brandColor"]) is None
    ):
        errors.append("extensions.com.openai.interface.brandColor must use #RRGGBB")
    for field in ("composerIcon", "logo", "logoDark"):
        if field in interface:
            errors.extend(
                _validate_relative_file(
                    package_root,
                    interface[field],
                    f"extensions.com.openai.interface.{field}",
                )
            )
    screenshots = interface.get("screenshots", [])
    if not isinstance(screenshots, list):
        errors.append("extensions.com.openai.interface.screenshots must be an array")
    else:
        for index, screenshot in enumerate(screenshots):
            errors.extend(
                _validate_relative_file(
                    package_root,
                    screenshot,
                    f"extensions.com.openai.interface.screenshots[{index}]",
                )
            )
    author = manifest.get("author")
    if isinstance(author, dict) and interface.get("developerName") != author.get("name"):
        errors.append("interface.developerName must match portable author.name")
    return errors


def validate_portable_manifest(
    package_root: Path,
    manifest: dict[str, Any],
    plugin_schema: dict[str, Any],
) -> list[str]:
    errors = validate_json_schema(manifest, plugin_schema)
    if set(manifest) - PORTABLE_FIELDS:
        errors.append("plugin.json contains fields outside the portable Agent Plugins manifest")
    for field in ("version", "description", "homepage", "repository", "license"):
        if not _non_empty_string(manifest.get(field)):
            errors.append(f"plugin.json field {field!r} is required by this catalog")
    version = manifest.get("version")
    if isinstance(version, str) and SEMVER_RE.fullmatch(version) is None:
        errors.append("plugin.json version must be strict semantic versioning")
    for field in ("homepage", "repository"):
        if field in manifest and not _is_https_url(manifest[field]):
            errors.append(f"plugin.json {field} must be an HTTPS URL")
    author = manifest.get("author")
    if not isinstance(author, dict) or not _non_empty_string(author.get("name")):
        errors.append("plugin.json author.name is required by this catalog")
    keywords = manifest.get("keywords")
    if (
        not isinstance(keywords, list)
        or not keywords
        or not all(_non_empty_string(item) for item in keywords)
    ):
        errors.append("plugin.json keywords must be a non-empty string array")
    extensions = manifest.get("extensions")
    if not isinstance(extensions, dict):
        errors.append("plugin.json extensions must contain com.openai")
        return errors
    codex_extension = extensions.get("com.openai")
    if not isinstance(codex_extension, dict):
        errors.append("plugin.json extensions.com.openai is required")
        return errors
    unknown = sorted(set(codex_extension) - CODEX_EXTENSION_FIELDS)
    if unknown:
        errors.append("extensions.com.openai has unsupported field(s): " + ", ".join(unknown))
    errors.extend(
        validate_openai_interface(package_root, manifest, codex_extension.get("interface"))
    )
    return errors


def _yaml_scalar(raw: str, label: str) -> Any:
    value = raw.strip()
    if not value:
        raise CatalogError(f"{label} must not be empty")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CatalogError(f"{label} has invalid quoted YAML: {exc}") from exc
        if not isinstance(parsed, str):
            raise CatalogError(f"{label} must be a string")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise CatalogError(f"{label} has invalid single-quoted YAML")
        return value[1:-1].replace("''", "'")
    if value in {"true", "false"}:
        return value == "true"
    if value in {"null", "~"} or value.startswith(("|", ">", "&", "*", "!")):
        raise CatalogError(f"{label} uses unsupported YAML syntax")
    if (
        value[0] in "-?:,[]{}#%@`"
        or ": " in value
        or " #" in value
        or re.fullmatch(
            r"(?i)(?:[-+]?(?:\d[\d_]*(?:\.\d[\d_]*)?|\.\d[\d_]*)"
            r"(?:e[-+]?\d+)?|[-+]?\.(?:inf|nan)|yes|no|on|off)",
            value,
        )
    ):
        raise CatalogError(f"{label} must quote this YAML string")
    return value


def _yaml_inline_string_list(raw: str, label: str) -> list[str]:
    value = raw.strip()
    if not value.startswith("[") or not value.endswith("]"):
        raise CatalogError(f"{label} must be a YAML string array")
    contents = value[1:-1].strip()
    if not contents:
        return []
    result: list[str] = []
    for index, item in enumerate(contents.split(",")):
        scalar = _yaml_scalar(item, f"{label}[{index}]")
        if not isinstance(scalar, str):
            raise CatalogError(f"{label}[{index}] must be a string")
        result.append(scalar)
    return result


def parse_skill_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    lines = contents.splitlines()
    if not lines or lines[0] != "---":
        raise CatalogError(f"{path} must start with YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise CatalogError(f"{path} frontmatter is not closed") from exc
    mapping: dict[str, Any] = {}
    for line_number, line in enumerate(lines[1:end], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise CatalogError(f"{path}:{line_number} must be a top-level key/value")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if key in mapping:
            raise CatalogError(f"{path}:{line_number} duplicates {key!r}")
        mapping[key] = _yaml_scalar(raw_value, f"{path}:{line_number}")
    return mapping, "\n".join(lines[end + 1 :]).strip()


def parse_openai_yaml(path: Path) -> dict[str, dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    result: dict[str, dict[str, Any]] = {}
    current: str | None = None
    current_list: tuple[str, str] | None = None
    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            raise CatalogError(f"{path}:{line_number} must not use tabs")
        if line.startswith("    - "):
            if current_list is None:
                raise CatalogError(f"{path}:{line_number} has an unexpected list item")
            section, key = current_list
            scalar = _yaml_scalar(line[6:], f"{path}:{line_number}")
            if not isinstance(scalar, str):
                raise CatalogError(f"{path}:{line_number} list item must be a string")
            result[section][key].append(scalar)
            continue
        current_list = None
        if not line.startswith(" "):
            if not line.endswith(":") or ":" in line[:-1]:
                raise CatalogError(f"{path}:{line_number} must start a mapping section")
            current = line[:-1].strip()
            if current in result:
                raise CatalogError(f"{path}:{line_number} duplicates section {current!r}")
            result[current] = {}
            continue
        if current is None:
            raise CatalogError(f"{path}:{line_number} has no parent section")
        if not line.startswith("  ") or line.startswith("   ") or ":" not in line:
            raise CatalogError(f"{path}:{line_number} must be a two-space key/value")
        key, raw_value = line[2:].split(":", 1)
        key = key.strip()
        if key in result[current]:
            raise CatalogError(f"{path}:{line_number} duplicates {current}.{key}")
        if current == "policy" and key == "products":
            if raw_value.strip():
                result[current][key] = _yaml_inline_string_list(
                    raw_value, f"{path}:{line_number}"
                )
            else:
                result[current][key] = []
                current_list = (current, key)
        else:
            result[current][key] = _yaml_scalar(raw_value, f"{path}:{line_number}")
    return result


def _validate_skill_asset(
    package_root: Path,
    skill_root: Path,
    raw_path: Any,
    label: str,
) -> list[str]:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        return [f"{label} must be a relative POSIX asset path"]
    relative = PurePosixPath(raw_path)
    if relative.is_absolute():
        return [f"{label} must be a relative POSIX asset path"]
    has_parent = ".." in relative.parts
    resolved = (skill_root / Path(*relative.parts)).resolve()
    if has_parent:
        allowed_root = (package_root / "assets").resolve()
        if not resolved.is_relative_to(allowed_root):
            return [f"{label} with '..' must resolve under the plugin assets directory"]
    else:
        normalized = PurePosixPath(*relative.parts)
        if not normalized.parts or normalized.parts[0] != "assets":
            return [f"{label} must resolve under the skill assets directory"]
        if not resolved.is_relative_to((skill_root / "assets").resolve()):
            return [f"{label} escapes the skill assets directory"]
    if not resolved.is_file():
        return [f"{label} points to a missing file: {raw_path}"]
    return []


def validate_skill(package_root: Path, skill_root: Path) -> list[str]:
    errors: list[str] = []
    skill_md = skill_root / "SKILL.md"
    try:
        frontmatter, body = parse_skill_frontmatter(skill_md)
    except CatalogError as exc:
        return [str(exc)]
    unknown = sorted(set(frontmatter) - SKILL_FRONTMATTER_FIELDS)
    if unknown:
        errors.append(
            f"{skill_md} uses frontmatter outside the catalog portability subset: "
            + ", ".join(unknown)
        )
    missing = sorted(SKILL_FRONTMATTER_REQUIRED - set(frontmatter))
    if missing:
        errors.append(f"{skill_md} is missing frontmatter field(s): {', '.join(missing)}")
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if (
        not isinstance(name, str)
        or SKILL_NAME_RE.fullmatch(name) is None
        or len(name) > SKILL_NAME_MAX_LENGTH
    ):
        errors.append(
            f"{skill_md} name must be lowercase hyphen-case and at most "
            f"{SKILL_NAME_MAX_LENGTH} characters"
        )
    elif name != skill_root.name:
        errors.append(f"{skill_md} name {name!r} must match directory {skill_root.name!r}")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{skill_md} description must be non-empty")
    elif len(description) > SKILL_DESCRIPTION_MAX_LENGTH or any(
        character in description for character in SKILL_DESCRIPTION_FORBIDDEN
    ):
        errors.append(f"{skill_md} description violates Codex length or character constraints")
    if not body:
        errors.append(f"{skill_md} must contain instructions after frontmatter")

    agent_path = skill_root / "agents" / "openai.yaml"
    if not agent_path.is_file():
        errors.append(f"{skill_root} must include agents/openai.yaml")
        return errors
    try:
        agent = parse_openai_yaml(agent_path)
    except CatalogError as exc:
        return errors + [str(exc)]
    unknown_sections = sorted(set(agent) - {"interface", "policy"})
    if unknown_sections:
        errors.append(f"{agent_path} has unknown section(s): {', '.join(unknown_sections)}")
    interface = agent.get("interface", {})
    unknown_interface = sorted(
        set(interface)
        - {
            "display_name",
            "short_description",
            "icon_small",
            "icon_large",
            "brand_color",
            "default_prompt",
        }
    )
    if unknown_interface:
        errors.append(f"{agent_path} has unknown interface field(s): {', '.join(unknown_interface)}")
    for field in ("display_name", "short_description", "default_prompt"):
        if not _non_empty_string(interface.get(field)):
            errors.append(f"{agent_path} interface.{field} must be non-empty")
    short = interface.get("short_description")
    if isinstance(short, str) and not 25 <= len(short) <= 64:
        errors.append(f"{agent_path} interface.short_description must be 25-64 characters")
    prompt = interface.get("default_prompt")
    if isinstance(name, str) and isinstance(prompt, str) and f"${name}" not in prompt:
        errors.append(f"{agent_path} interface.default_prompt must mention ${name}")
    if "brand_color" in interface and (
        not isinstance(interface["brand_color"], str)
        or HEX_COLOR_RE.fullmatch(interface["brand_color"]) is None
    ):
        errors.append(f"{agent_path} interface.brand_color must use #RRGGBB")
    for field in ("icon_small", "icon_large"):
        if field in interface:
            errors.extend(
                _validate_skill_asset(
                    package_root,
                    skill_root,
                    interface[field],
                    f"{agent_path} interface.{field}",
                )
            )
    policy = agent.get("policy", {})
    unknown_policy = sorted(set(policy) - {"allow_implicit_invocation", "products"})
    if unknown_policy:
        errors.append(f"{agent_path} has unknown policy field(s): {', '.join(unknown_policy)}")
    implicit = policy.get("allow_implicit_invocation", True)
    if not isinstance(implicit, bool):
        errors.append(f"{agent_path} policy.allow_implicit_invocation must be boolean")
    products = policy.get("products")
    if products is not None and products != ["codex"]:
        errors.append(f"{agent_path} policy.products must be ['codex'] for this catalog")
    if (
        isinstance(description, str)
        and description.lower().startswith("explicit invocation only")
        and implicit is not False
    ):
        errors.append(f"{agent_path} must disable implicit invocation for this explicit-only skill")
    return errors


def validate_package_tree(package_root: Path) -> tuple[list[str], bool]:
    errors: list[str] = []
    if package_root.is_symlink() or not package_root.is_dir():
        return [f"package root must be a real directory: {package_root}"], True
    unsafe_symlink = False
    file_count = 0
    total_bytes = 0
    for current, directories, filenames in os.walk(package_root, followlinks=False):
        current_path = Path(current)
        directories[:] = [name for name in directories if name != ".git"]
        for name in list(directories) + filenames:
            path = current_path / name
            try:
                stat = path.lstat()
            except OSError as exc:
                errors.append(f"cannot inspect {path}: {exc}")
                continue
            if path.is_symlink():
                errors.append(f"plugin packages must not contain symlinks: {path}")
                unsafe_symlink = True
                continue
            if path.is_file():
                file_count += 1
                total_bytes += stat.st_size
                if stat.st_size <= 1024 * 1024:
                    try:
                        contents = path.read_bytes()
                    except OSError:
                        continue
                    if b"\0" not in contents:
                        if GIT_LFS_POINTER_RE.match(contents):
                            errors.append(f"Git LFS pointer is not allowed in a package: {path}")
                        if any(pattern.search(contents) for pattern in SECRET_PATTERNS):
                            errors.append(f"possible embedded credential in {path}")
    if file_count > MAX_PACKAGE_FILES:
        errors.append(f"package has {file_count} files; limit is {MAX_PACKAGE_FILES}")
    if total_bytes > MAX_PACKAGE_BYTES:
        errors.append(f"package is {total_bytes} bytes; limit is {MAX_PACKAGE_BYTES}")
    return errors, unsafe_symlink


def validate_package(
    package_root: Path,
    plugin_schema: dict[str, Any],
    mcp_schema: dict[str, Any],
    *,
    expected_name: str | None = None,
) -> tuple[list[str], dict[str, Any] | None]:
    package_root = Path(os.path.abspath(package_root.expanduser()))
    errors, unsafe_tree = validate_package_tree(package_root)
    if unsafe_tree:
        # Stop before opening package-controlled paths that could escape through a symlink.
        return errors, None
    manifest_path = package_root / "plugin.json"
    try:
        manifest = load_json_object(manifest_path)
    except OverlayError as exc:
        return errors + [str(exc)], None
    errors.extend(validate_portable_manifest(package_root, manifest, plugin_schema))
    if expected_name is not None and manifest.get("name") != expected_name:
        errors.append(
            f"marketplace name {expected_name!r} does not match plugin.json name {manifest.get('name')!r}"
        )

    license_paths = [
        package_root / name for name in ("LICENSE", "LICENSE.md", "COPYING", "COPYING.md")
    ]
    if not any(path.is_file() and not path.is_symlink() for path in license_paths):
        errors.append(f"{package_root} must contain its own license file")

    skills_root = package_root / "skills"
    direct_skill_roots: list[Path] = []
    if not skills_root.is_dir() or skills_root.is_symlink():
        errors.append(f"{package_root} must contain a real skills directory")
    else:
        for child in sorted(skills_root.iterdir(), key=lambda path: path.name):
            if child.name.startswith("."):
                continue
            if child.is_file() and not child.is_symlink():
                continue
            if not child.is_dir() or child.is_symlink():
                errors.append(f"skills contains an unsupported entry: {child}")
                continue
            if not (child / "SKILL.md").is_file():
                continue
            direct_skill_roots.append(child)
        if not direct_skill_roots:
            errors.append(f"{skills_root} contains no discoverable skills")
        allowed_skill_files = {root / "SKILL.md" for root in direct_skill_roots}
        for skill_file in skills_root.rglob("SKILL.md"):
            if skill_file not in allowed_skill_files:
                errors.append(f"nested SKILL.md is not discoverable by Agent Plugins: {skill_file}")
        for skill_root in direct_skill_roots:
            errors.extend(validate_skill(package_root, skill_root))

    mcp_path = package_root / "mcp.json"
    if mcp_path.exists():
        try:
            mcp = load_json_object(mcp_path)
        except OverlayError as exc:
            errors.append(str(exc))
        else:
            errors.extend(validate_json_schema(mcp, mcp_schema, path="$mcp"))
            if mcp.get("$schema") != MCP_SCHEMA:
                errors.append("mcp.json must target the same Agent Plugins 1.0.0 release")
    try:
        errors.extend(check_overlay(package_root))
    except OverlayError as exc:
        errors.append(str(exc))
    return errors, manifest


def _canonical_github_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) == 2 and parts[1].endswith(".git") and len(parts[1]) > 4


def validate_marketplace_source(source: Any, label: str) -> list[str]:
    if not isinstance(source, dict):
        return [f"{label}.source must be an object"]
    kind = source.get("source")
    expected_fields = {"source", "url", "sha"}
    if kind == "git-subdir":
        expected_fields.add("path")
    elif kind != "url":
        return [f"{label}.source.source must be 'url' or 'git-subdir'"]
    errors: list[str] = []
    unknown = sorted(set(source) - expected_fields)
    if unknown:
        errors.append(f"{label}.source has unsupported field(s): {', '.join(unknown)}")
    missing = sorted(expected_fields - set(source))
    if missing:
        errors.append(f"{label}.source is missing: {', '.join(missing)}")
    if not _canonical_github_url(source.get("url")):
        errors.append(f"{label}.source.url must be canonical https://github.com/owner/repo.git")
    if not isinstance(source.get("sha"), str) or SHA_RE.fullmatch(source["sha"]) is None:
        errors.append(f"{label}.source.sha must be a full lowercase commit SHA")
    if kind == "git-subdir":
        raw_path = source.get("path")
        if not isinstance(raw_path, str):
            errors.append(f"{label}.source.path must be a string")
        else:
            relative = PurePosixPath(raw_path)
            if (
                not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
                or relative.is_absolute()
                or "\\" in raw_path
                or raw_path != relative.as_posix()
            ):
                errors.append(
                    f"{label}.source.path must be a canonical contained POSIX subdirectory "
                    "without a ./ prefix"
                )
    return errors


def validate_marketplace_entry(entry: Any, index: int) -> list[str]:
    label = f"plugins[{index}]"
    if not isinstance(entry, dict):
        return [f"{label} must be an object"]
    errors: list[str] = []
    unknown = sorted(set(entry) - MARKETPLACE_ENTRY_FIELDS)
    if unknown:
        errors.append(f"{label} has unknown field(s): {', '.join(unknown)}")
    required = MARKETPLACE_ENTRY_FIELDS
    missing = sorted(required - set(entry))
    if missing:
        errors.append(f"{label} is missing: {', '.join(missing)}")
    name = entry.get("name")
    if not isinstance(name, str) or PLUGIN_NAME_RE.fullmatch(name) is None or len(name) > 64:
        errors.append(f"{label}.name is not a valid Agent Plugins name")
    errors.extend(validate_marketplace_source(entry.get("source"), label))
    policy = entry.get("policy")
    if not isinstance(policy, dict):
        errors.append(f"{label}.policy must be an object")
    else:
        unknown_policy = sorted(
            set(policy) - {"installation", "authentication", "products"}
        )
        if unknown_policy:
            errors.append(f"{label}.policy has unknown field(s): {', '.join(unknown_policy)}")
        if policy.get("installation") not in {
            "NOT_AVAILABLE",
            "AVAILABLE",
            "INSTALLED_BY_DEFAULT",
        }:
            errors.append(f"{label}.policy.installation is invalid")
        if policy.get("authentication") not in {"ON_INSTALL", "ON_USE"}:
            errors.append(f"{label}.policy.authentication is invalid")
        if policy.get("products") != ["CODEX"]:
            errors.append(f"{label}.policy.products must be ['CODEX'] for this catalog")
    if not _non_empty_string(entry.get("category")):
        errors.append(f"{label}.category must be non-empty")
    return errors


def expected_marketplace_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    expected = {field: manifest[field] for field in MARKETPLACE_METADATA_FIELDS if field in manifest}
    extensions = manifest.get("extensions", {})
    codex_extension = extensions.get("com.openai", {}) if isinstance(extensions, dict) else {}
    if isinstance(codex_extension, dict) and "interface" in codex_extension:
        expected["interface"] = codex_extension["interface"]
    return expected


def validate_marketplace_metadata(
    entry: dict[str, Any],
    manifest: dict[str, Any],
    label: str,
) -> list[str]:
    errors: list[str] = []
    expected = expected_marketplace_metadata(manifest)
    for field, value in expected.items():
        if entry.get(field) != value:
            errors.append(f"{label}.{field} does not match the pinned plugin.json")
    interface = expected.get("interface")
    if isinstance(interface, dict) and entry.get("category") != interface.get("category"):
        errors.append(f"{label}.category must match extensions.com.openai.interface.category")
    return errors


def _run_git(arguments: list[str], *, cwd: Path | None = None) -> str:
    command = ["git", "-c", "protocol.file.allow=never", "-c", "core.hooksPath=/dev/null"]
    command.extend(arguments)
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        raise CatalogError(f"Git source materialization failed: {stderr.strip() or exc}") from exc
    return result.stdout.strip()


@contextlib.contextmanager
def materialize_remote_package(entry: dict[str, Any]) -> Iterator[Path]:
    source = entry["source"]
    with tempfile.TemporaryDirectory(prefix=f"codex-catalog-{entry['name']}-") as temp:
        checkout = Path(temp) / "checkout"
        checkout.mkdir()
        _run_git(["init", "--quiet"], cwd=checkout)
        _run_git(["remote", "add", "origin", source["url"]], cwd=checkout)
        subdir: str | None = None
        if source["source"] == "git-subdir":
            subdir = source["path"]
            _run_git(["sparse-checkout", "init", "--no-cone"], cwd=checkout)
            _run_git(["sparse-checkout", "set", "--no-cone", "--", subdir], cwd=checkout)
        _run_git(
            ["fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", source["sha"]],
            cwd=checkout,
        )
        _run_git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=checkout)
        actual_sha = _run_git(["rev-parse", "HEAD"], cwd=checkout)
        if actual_sha != source["sha"]:
            raise CatalogError(
                f"fetched SHA {actual_sha} does not match requested SHA {source['sha']}"
            )
        package_root = checkout if subdir is None else checkout / Path(*PurePosixPath(subdir).parts)
        if package_root.is_symlink() or not package_root.is_dir():
            raise CatalogError(f"pinned package root does not exist: {source.get('path', '.')}")
        yield package_root


def parse_package_overrides(raw_values: list[str]) -> tuple[dict[str, Path], list[str]]:
    overrides: dict[str, Path] = {}
    errors: list[str] = []
    for raw in raw_values:
        explicit_name: str | None = None
        raw_path = raw
        if "=" in raw:
            explicit_name, raw_path = raw.split("=", 1)
        path = Path(os.path.abspath(Path(raw_path).expanduser()))
        if path.is_symlink() or not path.is_dir():
            errors.append(f"--package-root must name a real directory: {raw!r}")
            continue
        if (path / "plugin.json").is_symlink():
            errors.append(f"--package-root plugin.json must not be a symlink: {raw!r}")
            continue
        try:
            manifest = load_json_object(path / "plugin.json")
        except OverlayError as exc:
            errors.append(str(exc))
            continue
        name = explicit_name or manifest.get("name")
        if not isinstance(name, str) or PLUGIN_NAME_RE.fullmatch(name) is None:
            errors.append(f"cannot infer a valid plugin name for --package-root {raw!r}")
            continue
        if name in overrides:
            errors.append(f"duplicate --package-root override for {name}")
            continue
        overrides[name] = path
    return overrides, errors


def validate_catalog(
    repo_root: Path,
    *,
    marketplace_path: Path,
    package_overrides: dict[str, Path] | None = None,
    fetch: bool = False,
) -> tuple[list[str], int, int]:
    package_overrides = package_overrides or {}
    errors, schemas = validate_standard_lock(repo_root)
    if set(schemas) != {"plugin", "mcp"}:
        return errors, 0, 0
    try:
        marketplace = load_json_object(marketplace_path)
    except OverlayError as exc:
        return errors + [str(exc)], 0, 0
    unknown_root = sorted(set(marketplace) - {"name", "interface", "plugins"})
    if unknown_root:
        errors.append("marketplace has unknown field(s): " + ", ".join(unknown_root))
    name = marketplace.get("name")
    if not isinstance(name, str) or MARKETPLACE_NAME_RE.fullmatch(name) is None:
        errors.append("marketplace.name must use ASCII letters, digits, underscores, or hyphens")
    interface = marketplace.get("interface")
    if not isinstance(interface, dict) or set(interface) != {"displayName"} or not _non_empty_string(
        interface.get("displayName") if isinstance(interface, dict) else None
    ):
        errors.append("marketplace.interface must contain only a non-empty displayName")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        return errors + ["marketplace.plugins must be an array"], 0, 0

    try:
        release_lock = load_drift_lock(repo_root / "upstreams.lock.json")
        validate_release_lock_structure(release_lock)
    except DriftLockError as exc:
        errors.append(str(exc))
    else:
        locked_marketplace_path = (repo_root / release_lock["marketplace"]["path"]).resolve()
        if marketplace_path.resolve() != locked_marketplace_path:
            release_lock = {}
    if release_lock:
        lock_entries = {
            entry.get("name"): entry for entry in plugins if isinstance(entry, dict)
        }
        if set(lock_entries) != set(release_lock["packages"]):
            errors.append("marketplace package names do not match upstreams.lock.json")
        for plugin_name, package in release_lock["packages"].items():
            entry = lock_entries.get(plugin_name)
            if not isinstance(entry, dict):
                continue
            current = package["current"]
            expected_source = {
                "source": "url" if current["plugin_root"] == "." else "git-subdir",
                "url": package["repository"] + ".git",
                "sha": current["commit"],
            }
            if current["plugin_root"] != ".":
                expected_source["path"] = current["plugin_root"]
            if entry.get("version") != current["version"]:
                errors.append(f"{plugin_name}: marketplace version does not match upstreams.lock.json")
            if entry.get("source") != expected_source:
                errors.append(f"{plugin_name}: marketplace source does not match upstreams.lock.json")

    seen: set[str] = set()
    validated_packages: set[str] = set()
    for index, entry in enumerate(plugins):
        entry_errors = validate_marketplace_entry(entry, index)
        errors.extend(entry_errors)
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        plugin_name = entry["name"]
        if plugin_name in seen:
            errors.append(f"duplicate marketplace plugin name: {plugin_name}")
        seen.add(plugin_name)
        if entry_errors:
            continue
        package_root = package_overrides.get(plugin_name)
        if package_root is not None:
            package_errors, manifest = validate_package(
                package_root,
                schemas["plugin"],
                schemas["mcp"],
                expected_name=plugin_name,
            )
            errors.extend(f"{plugin_name}: {error}" for error in package_errors)
            if manifest is not None:
                errors.extend(
                    validate_marketplace_metadata(entry, manifest, f"plugins[{index}]")
                )
            validated_packages.add(plugin_name)
        elif fetch:
            try:
                with materialize_remote_package(entry) as fetched_root:
                    package_errors, manifest = validate_package(
                        fetched_root,
                        schemas["plugin"],
                        schemas["mcp"],
                        expected_name=plugin_name,
                    )
                    errors.extend(f"{plugin_name}: {error}" for error in package_errors)
                    if manifest is not None:
                        errors.extend(
                            validate_marketplace_metadata(entry, manifest, f"plugins[{index}]")
                        )
                    validated_packages.add(plugin_name)
            except CatalogError as exc:
                errors.append(f"{plugin_name}: {exc}")

    for plugin_name, package_root in package_overrides.items():
        if plugin_name in validated_packages:
            continue
        package_errors, _ = validate_package(
            package_root,
            schemas["plugin"],
            schemas["mcp"],
            expected_name=plugin_name,
        )
        errors.extend(f"{plugin_name}: {error}" for error in package_errors)
        validated_packages.add(plugin_name)
    return errors, len(plugins), len(validated_packages)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parent.parent
    parser.add_argument("--repo-root", type=Path, default=default_root)
    parser.add_argument(
        "--marketplace",
        type=Path,
        help="marketplace.json path (default: <repo>/.agents/plugins/marketplace.json)",
    )
    parser.add_argument(
        "--package-root",
        action="append",
        default=[],
        metavar="[NAME=]PATH",
        help="validate a local package instead of fetching its pinned source",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="fetch and inspect immutable sources without executing package code",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    marketplace_path = (
        args.marketplace.expanduser().resolve()
        if args.marketplace is not None
        else repo_root / ".agents" / "plugins" / "marketplace.json"
    )
    overrides, override_errors = parse_package_overrides(args.package_root)
    errors, entry_count, package_count = validate_catalog(
        repo_root,
        marketplace_path=marketplace_path,
        package_overrides=overrides,
        fetch=args.fetch,
    )
    errors = override_errors + errors
    if errors:
        print("Catalog validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"Catalog validation passed: {entry_count} marketplace entr"
        f"{'y' if entry_count == 1 else 'ies'}, {package_count} package(s) inspected."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
