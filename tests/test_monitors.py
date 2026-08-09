from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import monitor_adapters  # noqa: E402
import monitor_standards  # noqa: E402
import reconcile_monitor_issue as reconciler  # noqa: E402
from monitor_lib import GitHubClient  # noqa: E402
from monitor_lib import MonitorError  # noqa: E402
from monitor_lib import build_report  # noqa: E402
from monitor_lib import finding  # noqa: E402
from monitor_lib import load_json  # noqa: E402
from monitor_lib import safe_relative_path  # noqa: E402
from monitor_lib import validate_release_lock_structure  # noqa: E402


class FakeHTTPResponse:
    def __init__(self, value: object, status: int = 200) -> None:
        self.data = json.dumps(value).encode("utf-8")
        self.status = status
        self.headers = {"Content-Length": str(len(self.data))}

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.data if size < 0 else self.data[:size]


class StandardsFixtureClient:
    def __init__(self, lock: dict[str, object], *, newer: bool = False) -> None:
        self.lock = lock
        self.newer = newer
        plugins = lock["agent_plugins"]
        skills = lock["agent_skills"]
        self.files: dict[tuple[str, str], bytes] = {}
        for record in (
            plugins["specification"],
            plugins["specification_license"],
            plugins["schemas_license"],
            skills["specification"],
            skills["license"],
        ):
            self.files[(record["path"], record["sha256"])] = (
                REPO_ROOT / record["vendored_path"]
            ).read_bytes()
        for schema in plugins["schemas"].values():
            self.files[(schema["upstream_path"], schema["sha256"])] = (
                REPO_ROOT / schema["path"]
            ).read_bytes()

    def read_file(self, repository: str, path: str, ref: str) -> bytes:
        del repository, ref
        matches = [value for (locked_path, _), value in self.files.items() if locked_path == path]
        if not matches:
            raise MonitorError(f"unexpected fixture path {path}")
        return matches[0]

    def commit_sha(self, repository: str, ref: str) -> str:
        if "agentplugins" in repository:
            return self.lock["agent_plugins"]["commit"]
        return self.lock["agent_skills"]["commit"]

    def directory_entries(self, repository: str, path: str, ref: str) -> list[dict[str, str]]:
        del repository, path, ref
        entries = [{"type": "file", "name": "1.0.0.md"}]
        if self.newer:
            entries.append({"type": "file", "name": "1.1.0.md"})
        return entries


class IssueFixtureClient:
    def __init__(self, issues: list[dict[str, object]]) -> None:
        self.issues = issues
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []

    def get_json(self, path: str, *, query: dict[str, object]) -> list[dict[str, object]]:
        self.calls.append(("GET", path, query))
        if path.endswith("/labels"):
            return [
                {"name": "automated-drift"},
                {"name": "monitor:agent-standards"},
            ]
        return self.issues

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del query
        self.calls.append((method, path, body))
        return {}


class MonitorTests(unittest.TestCase):
    def test_release_lock_is_strict_and_marketplace_pins_match(self) -> None:
        lock = load_json(REPO_ROOT / "upstreams.lock.json")
        validate_release_lock_structure(lock)
        findings, entries = monitor_adapters._marketplace_findings(REPO_ROOT, lock)
        self.assertEqual([], findings)
        self.assertEqual(set(lock["packages"]), set(entries))

    def test_release_lock_rejects_truncated_hashes(self) -> None:
        lock = copy.deepcopy(load_json(REPO_ROOT / "upstreams.lock.json"))
        lock["adapters"]["caveman"]["upstream"]["files"]["agents/openai.yaml"] = "abc"
        with self.assertRaises(MonitorError):
            validate_release_lock_structure(lock)

    def test_release_lock_rejects_policy_shrinkage(self) -> None:
        original = load_json(REPO_ROOT / "upstreams.lock.json")

        missing_adapter = copy.deepcopy(original)
        del missing_adapter["adapters"]["adhd"]
        with self.assertRaises(MonitorError):
            validate_release_lock_structure(missing_adapter)

        missing_derived_adapter = copy.deepcopy(original)
        del missing_derived_adapter["adapters"]["voice-edit"]
        with self.assertRaises(MonitorError):
            validate_release_lock_structure(missing_derived_adapter)

        missing_package = copy.deepcopy(original)
        del missing_package["packages"]["side-refresh"]
        with self.assertRaises(MonitorError):
            validate_release_lock_structure(missing_package)

        collapsed_rollback = copy.deepcopy(original)
        collapsed_rollback["packages"]["side-refresh"]["previous"]["commit"] = (
            collapsed_rollback["packages"]["side-refresh"]["current"]["commit"]
        )
        with self.assertRaises(MonitorError):
            validate_release_lock_structure(collapsed_rollback)

        weakened_invariant = copy.deepcopy(original)
        weakened_invariant["adapters"]["adhd"]["invariants"][0]["assertions"] = [
            {"op": "file_exists", "path": "plugin.json"}
        ]
        with self.assertRaises(MonitorError):
            validate_release_lock_structure(weakened_invariant)

        weakened_text = copy.deepcopy(original)
        weakened_text["adapters"]["adhd"]["invariants"][1]["assertions"][0][
            "value"
        ] = "a"
        with self.assertRaises(MonitorError):
            validate_release_lock_structure(weakened_text)

    def test_release_lock_rejects_derived_provenance_downgrades(self) -> None:
        original = load_json(REPO_ROOT / "upstreams.lock.json")

        wrong_relationship = copy.deepcopy(original)
        wrong_relationship["adapters"]["voice-edit"]["derived_provenance"][
            "repository_relationship"
        ] = "github-fork"

        wrong_license = copy.deepcopy(original)
        wrong_license["adapters"]["voice-edit"]["derived_provenance"][
            "source_license"
        ] = "UNKNOWN"

        disguised_as_fork = copy.deepcopy(original)
        provenance = disguised_as_fork["adapters"]["voice-edit"].pop(
            "derived_provenance"
        )
        disguised_as_fork["adapters"]["voice-edit"]["fork_provenance"] = {
            "must_be_fork": True,
            "parent_repository": provenance["upstream_repository"],
        }

        retained_license_changed = copy.deepcopy(original)
        retained_license_changed["packages"]["voice-edit"]["current"]["files"][
            "LICENSES/no-ai-slop-MIT.txt"
        ] = "f" * 64

        for label, changed in (
            ("wrong relationship", wrong_relationship),
            ("wrong license", wrong_license),
            ("disguised as fork", disguised_as_fork),
            ("changed retained license", retained_license_changed),
        ):
            with self.subTest(label=label), self.assertRaises(MonitorError):
                validate_release_lock_structure(changed)

    def test_release_lock_rejects_placeholders_and_impossible_counts(self) -> None:
        original = load_json(REPO_ROOT / "upstreams.lock.json")
        mutations = (
            ("placeholder commit", lambda lock: lock["packages"]["adhd"]["current"].__setitem__("commit", "0" * 40)),
            ("negative ruleset", lambda lock: lock["packages"]["adhd"]["tag_ruleset"].__setitem__("id", -1)),
            ("impossible file count", lambda lock: lock["packages"]["adhd"]["current"].__setitem__("file_count", 1)),
            ("negative workflow", lambda lock: lock["adapters"]["caveman"]["safety_checks"]["disabled_sync_workflow"].__setitem__("id", -1)),
            ("unprotected tag", lambda lock: lock["packages"]["adhd"]["current"].__setitem__("tag", "unprotected-release")),
            ("immutable adapter ref", lambda lock: lock["adapters"]["adhd"].__setitem__("adapter_branch", lock["packages"]["adhd"]["current"]["commit"])),
            ("immutable upstream ref", lambda lock: lock["adapters"]["adhd"]["upstream"].__setitem__("default_branch", lock["adapters"]["adhd"]["upstream"]["baseline_commit"])),
            ("unwatched source path", lambda lock: lock["adapters"]["adhd"]["upstream"].__setitem__("source_path", "README.md")),
            ("different workflow", lambda lock: lock["adapters"]["caveman"]["safety_checks"]["disabled_sync_workflow"].__setitem__("id", 1)),
            ("redirected package", lambda lock: lock["packages"]["adhd"].__setitem__("repository", "https://github.com/example/adhd")),
            ("different ruleset", lambda lock: lock["packages"]["adhd"]["tag_ruleset"].__setitem__("id", 1)),
            ("redirected upstream", lambda lock: (lock["adapters"]["adhd"]["upstream"].__setitem__("repository", "https://github.com/example/adhd"), lock["adapters"]["adhd"]["fork_provenance"].__setitem__("parent_repository", "https://github.com/example/adhd"))),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(original)
                mutate(changed)
                with self.assertRaises(MonitorError):
                    validate_release_lock_structure(changed)

    def test_safe_relative_path_rejects_normalized_aliases(self) -> None:
        for value in ("a//b", "a/./b"):
            with self.subTest(value=value), self.assertRaises(MonitorError):
                safe_relative_path(value)

    def test_ruleset_check_tolerates_hidden_bypass_actors(self) -> None:
        expected = {
            "id": 7,
            "enforcement": "active",
            "include": ["refs/tags/codex-plugin-v*"],
            "required_rule_types": ["deletion", "non_fast_forward"],
            "bypass_actors": [],
        }
        remote = {
            "id": 7,
            "target": "tag",
            "enforcement": "active",
            "conditions": {
                "ref_name": {
                    "include": ["refs/tags/codex-plugin-v*"],
                    "exclude": [],
                }
            },
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        }
        findings = monitor_adapters._ruleset_findings("fixture", expected, remote)
        report = build_report("fixture-monitor", findings)
        self.assertEqual("CLEAN", report["status"])
        self.assertEqual(
            ["tag-protection-bypass-visibility-limited"],
            [item["code"] for item in report["findings"]],
        )

    def test_repository_provenance_distinguishes_forks_and_derivatives(self) -> None:
        fork_adapter = {
            "fork_provenance": {
                "parent_repository": "https://github.com/example/upstream"
            }
        }
        derived_adapter = {"derived_provenance": {}}

        clean_fork = monitor_adapters._repository_provenance_findings(
            "forked",
            fork_adapter,
            {
                "fork": True,
                "parent": {"html_url": "https://github.com/example/upstream"},
            },
        )
        clean_derived = monitor_adapters._repository_provenance_findings(
            "derived", derived_adapter, {"fork": False, "parent": None}
        )
        wrong_parent = monitor_adapters._repository_provenance_findings(
            "forked",
            fork_adapter,
            {
                "fork": True,
                "parent": {"html_url": "https://github.com/example/different"},
            },
        )
        unexpected_fork = monitor_adapters._repository_provenance_findings(
            "derived", derived_adapter, {"fork": True}
        )

        self.assertEqual([], clean_fork)
        self.assertEqual([], clean_derived)
        self.assertEqual(["fork-parent-mismatch"], [item.code for item in wrong_parent])
        self.assertEqual(
            ["derived-repository-relationship-mismatch"],
            [item.code for item in unexpected_fork],
        )

    def test_derived_provenance_and_retained_license_are_verified(self) -> None:
        package_repository = "https://github.com/andydrewie/voice-edit"
        upstream_repository = "https://github.com/petergyang/no-ai-slop"
        package_commit = "a" * 40
        upstream_commit = "b" * 40
        source_license = b"reviewed MIT license\n"
        provenance = {
            "format_version": 1,
            "package": "voice-edit",
            "relationship": "codex_adaptation",
            "primary_source": {
                "repository": upstream_repository,
                "baseline_commit": upstream_commit,
                "license": "MIT",
                "license_path": "LICENSES/no-ai-slop-MIT.txt",
            },
        }
        notice = (
            f"Repository: {upstream_repository}\n"
            f"Reviewed baseline: {upstream_commit}\n"
            "License: MIT\n"
        ).encode()

        class DerivedFixtureClient:
            def __init__(self, *, provenance_bytes: bytes, retained_license: bytes) -> None:
                self.provenance_bytes = provenance_bytes
                self.retained_license = retained_license

            def read_file(self, repository: str, path: str, ref: str) -> bytes:
                if repository == upstream_repository:
                    self.assert_upstream(path, ref)
                    return source_license
                if repository != package_repository or ref != package_commit:
                    raise AssertionError((repository, path, ref))
                return {
                    "PROVENANCE.json": self.provenance_bytes,
                    "THIRD_PARTY_NOTICES.md": notice,
                    "LICENSES/no-ai-slop-MIT.txt": self.retained_license,
                }[path]

            @staticmethod
            def assert_upstream(path: str, ref: str) -> None:
                if path != "LICENSE" or ref != upstream_commit:
                    raise AssertionError((path, ref))

        adapter = {
            "derived_provenance": {
                "repository_relationship": "independent-derived",
                "upstream_repository": upstream_repository,
                "source_license": "MIT",
                "provenance_path": "PROVENANCE.json",
                "notice_path": "THIRD_PARTY_NOTICES.md",
                "source_license_path": "LICENSES/no-ai-slop-MIT.txt",
            },
            "upstream": {
                "repository": upstream_repository,
                "baseline_commit": upstream_commit,
            },
        }

        clean: list[object] = []
        monitor_adapters._check_derived_provenance(
            DerivedFixtureClient(
                provenance_bytes=json.dumps(provenance).encode(),
                retained_license=source_license,
            ),
            clean,
            name="voice-edit",
            repository=package_repository,
            commit=package_commit,
            plugin_root=".",
            adapter=adapter,
        )
        self.assertEqual([], clean)

        provenance["primary_source"]["baseline_commit"] = "c" * 40
        drift: list[object] = []
        monitor_adapters._check_derived_provenance(
            DerivedFixtureClient(
                provenance_bytes=json.dumps(provenance).encode(),
                retained_license=b"different license\n",
            ),
            drift,
            name="voice-edit",
            repository=package_repository,
            commit=package_commit,
            plugin_root=".",
            adapter=adapter,
        )
        self.assertEqual(
            {"derived-provenance-mismatch", "retained-source-license-mismatch"},
            {item.code for item in drift},
        )

    def test_status_contract_and_fingerprint_are_stable(self) -> None:
        findings = [
            finding("REVIEW_REQUIRED", "changed", "upstream", "bytes changed"),
            finding("INFO", "advanced", "head", "head advanced"),
        ]
        first = build_report("fixture-monitor", findings)
        second = build_report("fixture-monitor", list(reversed(findings)))
        self.assertEqual("REVIEW_REQUIRED", first["status"])
        self.assertEqual(10, first["exit_code"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        aggregate = build_report(
            "fixture-monitor",
            findings + [finding("SOURCE_UNAVAILABLE", "network", "api", "offline")],
        )
        self.assertEqual("SOURCE_UNAVAILABLE", aggregate["status"])
        self.assertEqual(30, aggregate["exit_code"])

    def test_cli_failure_still_writes_requested_report(self) -> None:
        for module in (monitor_standards, monitor_adapters):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "report.json"
                argv = [module.__file__, "--output", str(output)]
                with (
                    mock.patch.object(module, "run_monitor", side_effect=MonitorError("bad lock")),
                    mock.patch.object(sys, "argv", argv),
                ):
                    exit_code = module.main()
                self.assertEqual(64, exit_code)
                self.assertEqual("CONFIG_OR_USAGE_ERROR", json.loads(output.read_text())["status"])

    def test_github_client_reads_mocked_rest_bytes(self) -> None:
        payload = b"portable bytes\n"
        encoded = base64.b64encode(payload).decode("ascii")
        response = FakeHTTPResponse(
            {
                "type": "file",
                "encoding": "base64",
                "content": f"{encoded[:8]}\n{encoded[8:]}\n",
                "size": len(payload),
            }
        )
        client = GitHubClient(api_url="https://api.invalid", attempts=1)
        with mock.patch("urllib.request.urlopen", return_value=response):
            actual = client.read_file(
                "https://github.com/example/repository", "plugin.json", "a" * 40
            )
        self.assertEqual(payload, actual)

    def test_full_tree_digest_algorithm_uses_paths_modes_sizes_and_bytes(self) -> None:
        first = b"alpha\n"
        second = b"beta\n"
        blobs = {
            "a" * 40: first,
            "b" * 40: second,
        }

        class InventoryClient(GitHubClient):
            def tree_sha(self, repository: str, commit: str, path: str) -> str:
                del repository, commit, path
                return "c" * 40

            def get_json(self, path: str, *, query: dict[str, object] | None = None) -> object:
                if "/git/trees/" in path:
                    return {
                        "truncated": False,
                        "tree": [
                            {"path": "a.txt", "mode": "100644", "type": "blob", "sha": "a" * 40},
                            {"path": "bin/b", "mode": "100755", "type": "blob", "sha": "b" * 40},
                        ],
                    }
                sha = path.rsplit("/", 1)[-1]
                data = blobs[sha]
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(data).decode("ascii"),
                    "size": len(data),
                }

        inventory = InventoryClient().file_inventory(
            "https://github.com/example/repository", "d" * 40, "."
        )
        expected = hashlib.sha256()
        expected.update(b"codex-skills-tree-v1\0")
        for path, mode, data in (
            ("a.txt", "100644", first),
            ("bin/b", "100755", second),
        ):
            path_bytes = path.encode()
            mode_bytes = mode.encode()
            expected.update(len(path_bytes).to_bytes(4, "big"))
            expected.update(path_bytes)
            expected.update(len(mode_bytes).to_bytes(1, "big"))
            expected.update(mode_bytes)
            expected.update(len(data).to_bytes(8, "big"))
            expected.update(data)
        self.assertEqual(expected.hexdigest(), inventory["tree_sha256"])
        self.assertEqual(2, inventory["file_count"])

    def test_standards_fixture_is_clean_and_new_version_requests_review(self) -> None:
        lock = load_json(REPO_ROOT / "standards.lock.json")
        clean = monitor_standards.run_monitor(REPO_ROOT, StandardsFixtureClient(lock))
        self.assertEqual("CLEAN", clean["status"])
        drift = monitor_standards.run_monitor(
            REPO_ROOT, StandardsFixtureClient(lock, newer=True)
        )
        self.assertEqual("REVIEW_REQUIRED", drift["status"])
        self.assertTrue(any(item["code"] == "new-standard-version" for item in drift["findings"]))

    def test_issue_reconciler_deduplicates_unchanged_fingerprint(self) -> None:
        report = build_report(
            "agent-plugin-standards",
            [finding("REVIEW_REQUIRED", "changed", "upstream", "bytes changed")],
        )
        body = reconciler._body(report)
        client = IssueFixtureClient([{"number": 7, "state": "open", "body": body}])
        reconciler.reconcile(report, client, "example/catalog")
        self.assertEqual(["GET", "GET"], [call[0] for call in client.calls])

    def test_issue_rendering_escapes_html_from_remote_paths(self) -> None:
        escaped = reconciler._escape_markdown("<details>&remote</details>")
        self.assertIn("&lt;details&gt;", escaped)
        self.assertIn("&amp;remote", escaped)
        self.assertNotIn("<details>", escaped)

    def test_issue_reconciler_closes_managed_issue_after_clean_run(self) -> None:
        report = build_report("agent-plugin-standards", [])
        client = IssueFixtureClient(
            [
                {
                    "number": 7,
                    "state": "open",
                    "body": f"<!-- codex-skills-monitor id=agent-standards fingerprint={'a' * 64} -->\nold",
                }
            ]
        )
        reconciler.reconcile(report, client, "example/catalog")
        self.assertEqual(["GET", "POST", "PATCH"], [call[0] for call in client.calls])


if __name__ == "__main__":
    unittest.main()
