from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from generate_legacy_overlay import check_overlay  # noqa: E402
from generate_legacy_overlay import load_json_object  # noqa: E402
from generate_legacy_overlay import OverlayError  # noqa: E402
import validate_catalog as catalog  # noqa: E402


class CatalogToolTests(unittest.TestCase):
    def schemas(self) -> dict[str, dict[str, object]]:
        errors, schemas = catalog.validate_standard_lock(REPO_ROOT)
        self.assertEqual([], errors)
        self.assertEqual({"plugin", "mcp"}, set(schemas))
        return schemas

    def copied_fixture(self, temporary_root: str) -> Path:
        target = Path(temporary_root) / "demo-plugin"
        shutil.copytree(FIXTURES / "valid-plugin", target)
        return target

    def test_standard_lock_and_vendored_schemas_are_exact(self) -> None:
        schemas = self.schemas()
        self.assertEqual(catalog.AGENT_PLUGIN_SCHEMA, schemas["plugin"]["$id"])
        self.assertEqual(catalog.MCP_SCHEMA, schemas["mcp"]["$id"])
        self.assertEqual(
            "6539175bfcdf43085855183e86da40ea94b166547a72b47ae9a0a390516d3acb",
            catalog.sha256_file(
                REPO_ROOT / "vendor/agent-plugins-spec/1.0.0/mcp.schema.json"
            ),
        )
        self.assertEqual(
            "97a658b7dca3ce1b4c2266b95da300fa51d9dc4ade59d73168e5f9104272da18",
            catalog.sha256_file(
                REPO_ROOT / "vendor/agent-plugins-spec/1.0.0/specification.md"
            ),
        )
        self.assertEqual(
            "9e5f1b3c610b9c2da5c313bf81d577a7d1acec686bdb0384edefa6df0f90cd94",
            catalog.sha256_file(
                REPO_ROOT / "vendor/agent-plugins-spec/LICENSES/CC-BY-4.0.txt"
            ),
        )
        self.assertEqual(
            "b9079c0c10b7930e8c6a20ff2bc10cda2a3343c55185120e3f1116a1a529b220",
            catalog.sha256_file(
                REPO_ROOT / "vendor/agent-skills/unversioned/specification.mdx"
            ),
        )
        self.assertEqual(
            "9e5f1b3c610b9c2da5c313bf81d577a7d1acec686bdb0384edefa6df0f90cd94",
            catalog.sha256_file(
                REPO_ROOT / "vendor/agent-skills/LICENSES/CC-BY-4.0.txt"
            ),
        )
        self.assertEqual(
            "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
            catalog.sha256_file(
                REPO_ROOT / "vendor/agent-plugins-spec/LICENSES/Apache-2.0.txt"
            ),
        )

    def test_json_loader_rejects_nonstandard_numeric_constants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            path = Path(temporary_root) / "invalid.json"
            path.write_text('{"value": NaN}\n', encoding="utf-8")
            with self.assertRaises(OverlayError):
                load_json_object(path)

    def test_valid_fixture_and_local_marketplace_override(self) -> None:
        errors, entries, packages = catalog.validate_catalog(
            REPO_ROOT,
            marketplace_path=FIXTURES / "marketplace.valid.json",
            package_overrides={"demo-plugin": FIXTURES / "valid-plugin"},
        )
        self.assertEqual([], errors)
        self.assertEqual(1, entries)
        self.assertEqual(1, packages)

    def test_generated_overlay_is_deterministic_and_staleness_is_detected(self) -> None:
        self.assertEqual([], check_overlay(FIXTURES / "valid-plugin"))
        with tempfile.TemporaryDirectory() as temporary_root:
            package_root = self.copied_fixture(temporary_root)
            overlay_path = package_root / ".codex-plugin" / "plugin.json"
            overlay = load_json_object(overlay_path)
            overlay["version"] = "9.9.9"
            overlay_path.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
            errors = check_overlay(package_root)
        self.assertTrue(any("stale" in error for error in errors))

    def test_nested_skill_is_rejected_as_undiscoverable(self) -> None:
        schemas = self.schemas()
        with tempfile.TemporaryDirectory() as temporary_root:
            package_root = self.copied_fixture(temporary_root)
            nested = package_root / "skills" / "demo-skill" / "nested"
            nested.mkdir()
            shutil.copy(
                package_root / "skills" / "demo-skill" / "SKILL.md",
                nested / "SKILL.md",
            )
            errors, _ = catalog.validate_package(
                package_root,
                schemas["plugin"],
                schemas["mcp"],
                expected_name="demo-plugin",
            )
        self.assertTrue(any("nested SKILL.md" in error for error in errors))

    def test_package_symlink_stops_validation_before_files_are_opened(self) -> None:
        schemas = self.schemas()
        with tempfile.TemporaryDirectory() as temporary_root:
            package_link = Path(temporary_root) / "linked-package"
            package_link.symlink_to(FIXTURES / "valid-plugin", target_is_directory=True)
            errors, manifest = catalog.validate_package(
                package_link,
                schemas["plugin"],
                schemas["mcp"],
                expected_name="demo-plugin",
            )
        self.assertIsNone(manifest)
        self.assertTrue(any("real directory" in error for error in errors))

    def test_portable_manifest_rejects_unknown_root_fields(self) -> None:
        schemas = self.schemas()
        manifest = load_json_object(FIXTURES / "valid-plugin" / "plugin.json")
        manifest["commands"] = ["unsafe"]
        errors = catalog.validate_portable_manifest(
            FIXTURES / "valid-plugin", manifest, schemas["plugin"]
        )
        self.assertTrue(any("unknown field" in error for error in errors))

    def test_codex_apps_and_hooks_fail_closed_for_skill_only_catalog(self) -> None:
        schemas = self.schemas()
        manifest = load_json_object(FIXTURES / "valid-plugin" / "plugin.json")
        extension = manifest["extensions"]["com.openai"]
        extension["apps"] = 42
        extension["hooks"] = "hooks.json"
        errors = catalog.validate_portable_manifest(
            FIXTURES / "valid-plugin", manifest, schemas["plugin"]
        )
        self.assertTrue(any("apps" in error and "hooks" in error for error in errors))

    def test_skill_metadata_rejects_unvalidated_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            package_root = self.copied_fixture(temporary_root)
            agent_path = (
                package_root / "skills" / "demo-skill" / "agents" / "openai.yaml"
            )
            agent_path.write_text(
                agent_path.read_text(encoding="utf-8")
                + "dependencies:\n  tools:\n    - invalid\n",
                encoding="utf-8",
            )
            errors = catalog.validate_skill(
                package_root, package_root / "skills" / "demo-skill"
            )
        self.assertTrue(any("must not be empty" in error for error in errors))

    def test_yaml_subset_rejects_plain_scalars_real_yaml_would_reinterpret(self) -> None:
        with self.assertRaises(catalog.CatalogError):
            catalog._yaml_scalar("Use $demo: now", "prompt")
        with self.assertRaises(catalog.CatalogError):
            catalog._yaml_scalar("123", "description")
        with self.assertRaises(catalog.CatalogError):
            catalog._yaml_scalar("value # comment", "description")

    def test_skill_metadata_supports_codex_policy_and_asset_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            package_root = self.copied_fixture(temporary_root)
            skill_root = package_root / "skills" / "demo-skill"
            (skill_root / "assets").mkdir()
            (skill_root / "assets" / "small.svg").write_text("<svg/>\n", encoding="utf-8")
            (package_root / "assets").mkdir()
            (package_root / "assets" / "large.svg").write_text("<svg/>\n", encoding="utf-8")
            (skill_root / "agents" / "openai.yaml").write_text(
                "interface:\n"
                '  display_name: "Demo Skill"\n'
                '  short_description: "Validate portable Codex plugin fixtures"\n'
                "  icon_small: assets/small.svg\n"
                "  icon_large: ../../assets/large.svg\n"
                '  default_prompt: "Use $demo-skill to validate this fixture."\n'
                "policy:\n"
                "  allow_implicit_invocation: true\n"
                "  products:\n"
                "    - codex\n",
                encoding="utf-8",
            )
            errors = catalog.validate_skill(package_root, skill_root)
        self.assertEqual([], errors)

    def test_marketplace_requires_immutable_sha_and_canonical_subdir(self) -> None:
        source = {
            "source": "git-subdir",
            "url": "https://github.com/owner/repository.git",
            "path": "./skills/example",
            "ref": "main",
            "sha": "abc123",
        }
        errors = catalog.validate_marketplace_source(source, "plugins[0]")
        self.assertTrue(any("unsupported field" in error for error in errors))
        self.assertTrue(any("full lowercase commit SHA" in error for error in errors))
        self.assertTrue(any("without a ./ prefix" in error for error in errors))
        source.pop("ref")
        source["path"] = "skills/example"
        source["sha"] = "a" * 40
        self.assertEqual([], catalog.validate_marketplace_source(source, "plugins[0]"))

    def test_mcp_schema_requires_exactly_one_transport_shape(self) -> None:
        schemas = self.schemas()
        invalid = {
            "$schema": catalog.MCP_SCHEMA,
            "mcpServers": {"demo": {"type": "unknown", "url": "https://example.com"}},
        }
        errors = catalog.validate_json_schema(invalid, schemas["mcp"])
        self.assertTrue(any("exactly one" in error for error in errors))

    def test_valid_mcp_still_fails_closed_without_legacy_bridge(self) -> None:
        schemas = self.schemas()
        with tempfile.TemporaryDirectory() as temporary_root:
            package_root = self.copied_fixture(temporary_root)
            mcp = {
                "$schema": catalog.MCP_SCHEMA,
                "mcpServers": {
                    "demo": {
                        "type": "streamable-http",
                        "url": "https://example.com/mcp",
                    }
                },
            }
            (package_root / "mcp.json").write_text(
                json.dumps(mcp, indent=2) + "\n", encoding="utf-8"
            )
            errors, _ = catalog.validate_package(
                package_root,
                schemas["plugin"],
                schemas["mcp"],
                expected_name="demo-plugin",
            )
        self.assertTrue(any("legacy bridge is skill-only" in error for error in errors))

    def test_git_lfs_pointer_is_rejected_before_size_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            package_root = self.copied_fixture(temporary_root)
            (package_root / "large.bin").write_text(
                "version https://git-lfs.github.com/spec/v1\n"
                f"oid sha256:{'a' * 64}\n"
                "size 999999999\n",
                encoding="utf-8",
            )
            errors, unsafe_tree = catalog.validate_package_tree(package_root)
        self.assertFalse(unsafe_tree)
        self.assertTrue(any("Git LFS pointer" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
