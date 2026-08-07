#!/usr/bin/env python3
"""Verify immutable package releases and report reviewed-adapter upstream drift."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from monitor_lib import GitHubClient
from monitor_lib import MonitorError
from monitor_lib import UsageArgumentParser
from monitor_lib import build_report
from monitor_lib import finding
from monitor_lib import load_json
from monitor_lib import repository_path
from monitor_lib import repository_slug
from monitor_lib import sha256_bytes
from monitor_lib import validate_release_lock_structure
from monitor_lib import write_report
from monitor_lib import write_failure_report


TREE_FIELDS = ("git_tree_sha1", "tree_sha256", "file_count", "total_bytes")


def _source_unavailable(findings: list[Any], subject: str, exc: MonitorError) -> None:
    findings.append(finding("SOURCE_UNAVAILABLE", "remote-read-failed", subject, str(exc)))


def _compare_inventory(
    findings: list[Any],
    *,
    subject: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    severity: str,
) -> None:
    for field in TREE_FIELDS:
        if actual[field] != expected[field]:
            findings.append(
                finding(
                    severity,
                    f"{field}-mismatch",
                    subject,
                    f"The remotely read package inventory has a different {field}.",
                    expected=expected[field],
                    actual=actual[field],
                )
            )
    for path, expected_digest in expected.get("files", {}).items():
        actual_digest = actual["files"].get(path)
        if actual_digest != expected_digest:
            findings.append(
                finding(
                    severity,
                    "file-hash-mismatch",
                    f"{subject}:{path}",
                    "A watched file does not match its locked SHA-256.",
                    expected=expected_digest,
                    actual=actual_digest or "missing",
                )
            )


def _marketplace_findings(
    repo_root: Path, lock: dict[str, Any]
) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    findings: list[Any] = []
    try:
        marketplace = load_json(repo_root / lock["marketplace"]["path"])
    except MonitorError as exc:
        return [
            finding("INTEGRITY_FAILURE", "marketplace-unreadable", "marketplace", str(exc))
        ], {}
    if marketplace.get("name") != lock["marketplace"]["name"]:
        findings.append(
            finding(
                "INTEGRITY_FAILURE",
                "marketplace-name-mismatch",
                "marketplace",
                "Marketplace name does not match upstreams.lock.json.",
                expected=lock["marketplace"]["name"],
                actual=marketplace.get("name"),
            )
        )
    raw_entries = marketplace.get("plugins")
    if not isinstance(raw_entries, list):
        return findings + [
            finding(
                "INTEGRITY_FAILURE",
                "marketplace-plugins-invalid",
                "marketplace",
                "Marketplace plugins must be an array.",
            )
        ], {}
    entries: dict[str, dict[str, Any]] = {}
    for entry in raw_entries:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or name in entries:
            findings.append(
                finding(
                    "INTEGRITY_FAILURE",
                    "marketplace-entry-invalid",
                    "marketplace",
                    "Marketplace entries must have unique string names.",
                )
            )
            continue
        entries[name] = entry
    if set(entries) != set(lock["packages"]):
        findings.append(
            finding(
                "INTEGRITY_FAILURE",
                "marketplace-package-set-mismatch",
                "marketplace",
                "Marketplace and release lock package names differ.",
                expected=", ".join(sorted(lock["packages"])),
                actual=", ".join(sorted(entries)),
            )
        )
    for name, package in lock["packages"].items():
        entry = entries.get(name)
        if entry is None:
            continue
        current = package["current"]
        expected_url = package["repository"] + ".git"
        expected_source = {
            "source": "url" if current["plugin_root"] == "." else "git-subdir",
            "url": expected_url,
            "sha": current["commit"],
        }
        if current["plugin_root"] != ".":
            expected_source["path"] = current["plugin_root"]
        for field, expected in (
            ("version", current["version"]),
            ("source", expected_source),
        ):
            if entry.get(field) != expected:
                findings.append(
                    finding(
                        "INTEGRITY_FAILURE",
                        f"marketplace-{field}-mismatch",
                        name,
                        f"Marketplace {field} does not match the immutable release lock.",
                        expected=expected,
                        actual=entry.get(field),
                    )
                )
    return findings, entries


def _ruleset_findings(
    name: str, expected_ruleset: dict[str, Any], remote_ruleset: Any
) -> list[Any]:
    """Validate public ruleset guarantees without requiring cross-repo admin access."""

    findings: list[Any] = []
    conditions = remote_ruleset.get("conditions") if isinstance(remote_ruleset, dict) else None
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    remote_rules = remote_ruleset.get("rules") if isinstance(remote_ruleset, dict) else None
    actual_types = (
        sorted(
            item.get("type")
            for item in remote_rules
            if isinstance(item, dict) and isinstance(item.get("type"), str)
        )
        if isinstance(remote_rules, list)
        else []
    )
    checks = (
        ("id", expected_ruleset["id"], remote_ruleset.get("id") if isinstance(remote_ruleset, dict) else None),
        ("target", "tag", remote_ruleset.get("target") if isinstance(remote_ruleset, dict) else None),
        (
            "enforcement",
            expected_ruleset["enforcement"],
            remote_ruleset.get("enforcement") if isinstance(remote_ruleset, dict) else None,
        ),
        (
            "include",
            expected_ruleset["include"],
            ref_name.get("include") if isinstance(ref_name, dict) else None,
        ),
        ("exclude", [], ref_name.get("exclude") if isinstance(ref_name, dict) else None),
        ("rules", sorted(expected_ruleset["required_rule_types"]), actual_types),
    )
    for field, expected, actual in checks:
        if actual != expected:
            findings.append(
                finding(
                    "INTEGRITY_FAILURE",
                    "tag-protection-mismatch",
                    name,
                    f"The immutable tag ruleset has unexpected {field}.",
                    expected=expected,
                    actual=actual,
                )
            )

    if isinstance(remote_ruleset, dict) and "bypass_actors" in remote_ruleset:
        actual_bypass = remote_ruleset["bypass_actors"]
        if actual_bypass != expected_ruleset["bypass_actors"]:
            findings.append(
                finding(
                    "INTEGRITY_FAILURE",
                    "tag-protection-mismatch",
                    name,
                    "The immutable tag ruleset has unexpected bypass actors.",
                    expected=expected_ruleset["bypass_actors"],
                    actual=actual_bypass,
                )
            )
    else:
        findings.append(
            finding(
                "INFO",
                "tag-protection-bypass-visibility-limited",
                name,
                "GitHub hides cross-repository ruleset bypass actors from this least-privilege token; the monitor still verifies the active rules and the release tag target.",
            )
        )
    return findings


def _check_invariants(
    client: GitHubClient,
    findings: list[Any],
    *,
    name: str,
    repository: str,
    commit: str,
    plugin_root: str,
    invariants: list[dict[str, Any]],
) -> None:
    cache: dict[str, bytes] = {}

    def read(path: str, *, relative_to_plugin: bool) -> bytes | None:
        remote_path = repository_path(plugin_root, path) if relative_to_plugin else path
        if remote_path in cache:
            return cache[remote_path]
        try:
            cache[remote_path] = client.read_file(repository, remote_path, commit)
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} invariant file {remote_path}", exc)
            return None
        return cache[remote_path]

    for invariant in invariants:
        for assertion in invariant["assertions"]:
            left = read(assertion["path"], relative_to_plugin=True)
            if left is None:
                continue
            operation = assertion["op"]
            if operation == "file_exists":
                continue
            if operation in {"contains_utf8", "not_contains_utf8"}:
                try:
                    text = left.decode("utf-8")
                except UnicodeError:
                    findings.append(
                        finding(
                            "INTEGRITY_FAILURE",
                            "invariant-not-utf8",
                            f"{name}:{invariant['id']}",
                            f"{assertion['path']} is not valid UTF-8.",
                        )
                    )
                    continue
                present = assertion["value"] in text
                expected_present = operation == "contains_utf8"
                if present != expected_present:
                    findings.append(
                        finding(
                            "INTEGRITY_FAILURE",
                            "invariant-text-mismatch",
                            f"{name}:{invariant['id']}",
                            f"UTF-8 text assertion failed for {assertion['path']}.",
                            expected=("present: " if expected_present else "absent: ") + assertion["value"],
                            actual="present" if present else "absent",
                        )
                    )
            elif operation == "json_pointer_equals":
                try:
                    value: Any = json.loads(left.decode("utf-8"))
                    for raw_part in assertion["pointer"].split("/")[1:]:
                        if "~" in raw_part and any(
                            token not in {"~0", "~1"}
                            for token in re.findall(r"~.", raw_part)
                        ):
                            raise ValueError("invalid JSON pointer escape")
                        part = raw_part.replace("~1", "/").replace("~0", "~")
                        if isinstance(value, list):
                            value = value[int(part)]
                        else:
                            value = value[part]
                except (UnicodeError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
                    value = object()
                if value != assertion["value"]:
                    findings.append(
                        finding(
                            "INTEGRITY_FAILURE",
                            "invariant-json-pointer-mismatch",
                            f"{name}:{invariant['id']}",
                            f"JSON pointer {assertion['pointer']} has an unexpected value.",
                            expected=assertion["value"],
                            actual="missing-or-different",
                        )
                    )
            elif operation == "byte_equal":
                right_path = assertion["other_path"]
                right = read(right_path, relative_to_plugin=False)
                if right is not None and left != right:
                    findings.append(
                        finding(
                            "INTEGRITY_FAILURE",
                            "retained-path-mismatch",
                            f"{name}:{invariant['id']}",
                            "The retained selective-install file is not byte-identical.",
                            expected=sha256_bytes(left),
                            actual=sha256_bytes(right),
                        )
                    )


def run_monitor(repo_root: Path, client: GitHubClient) -> dict[str, Any]:
    findings: list[Any] = []
    try:
        lock = load_json(repo_root / "upstreams.lock.json")
        validate_release_lock_structure(lock)
    except MonitorError as exc:
        return build_report(
            "codex-skill-adapters",
            [finding("CONFIG_OR_USAGE_ERROR", "invalid-lock", "upstreams.lock.json", str(exc))],
        )

    marketplace_findings, _ = _marketplace_findings(repo_root, lock)
    findings.extend(marketplace_findings)

    current_inventories: dict[str, dict[str, Any]] = {}
    for name, package in sorted(lock["packages"].items()):
        repository = package["repository"]
        current = package["current"]
        try:
            inventory = client.file_inventory(
                repository, current["commit"], current["plugin_root"]
            )
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} current release", exc)
        else:
            current_inventories[name] = inventory
            _compare_inventory(
                findings,
                subject=f"{name} current release",
                expected=current,
                actual=inventory,
                severity="INTEGRITY_FAILURE",
            )
        try:
            tag_commit = client.resolve_tag(repository, current["tag"])
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} release tag", exc)
        else:
            if tag_commit != current["commit"]:
                findings.append(
                    finding(
                        "INTEGRITY_FAILURE",
                        "tag-target-mismatch",
                        name,
                        "The immutable release tag points at a different commit.",
                        expected=current["commit"],
                        actual=tag_commit,
                    )
                )
        ruleset = package["tag_ruleset"]
        slug = repository_slug(repository)
        try:
            remote_ruleset = client.get_json(
                f"/repos/{slug}/rulesets/{ruleset['id']}"
            )
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} tag-protection ruleset", exc)
        else:
            findings.extend(_ruleset_findings(name, ruleset, remote_ruleset))
        previous = package["previous"]
        try:
            previous_inventory = client.file_inventory(
                repository, previous["commit"], previous["path"]
            )
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} rollback release", exc)
        else:
            _compare_inventory(
                findings,
                subject=f"{name} rollback release",
                expected=previous,
                actual=previous_inventory,
                severity="INTEGRITY_FAILURE",
            )

    for name, adapter in sorted(lock["adapters"].items()):
        package = lock["packages"][adapter["package"]]
        repository = package["repository"]
        current = package["current"]
        slug = repository_slug(repository)
        try:
            metadata = client.get_json(f"/repos/{slug}")
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} fork provenance", exc)
        else:
            parent = metadata.get("parent") if isinstance(metadata, dict) else None
            actual_parent = parent.get("html_url") if isinstance(parent, dict) else None
            if not isinstance(metadata, dict) or metadata.get("fork") is not True:
                findings.append(
                    finding(
                        "INTEGRITY_FAILURE",
                        "fork-provenance-lost",
                        name,
                        "The adapter repository is no longer marked as a fork.",
                    )
                )
            if actual_parent != adapter["fork_provenance"]["parent_repository"]:
                findings.append(
                    finding(
                        "INTEGRITY_FAILURE",
                        "fork-parent-mismatch",
                        name,
                        "The GitHub fork parent no longer matches the reviewed upstream.",
                        expected=adapter["fork_provenance"]["parent_repository"],
                        actual=actual_parent or "missing",
                    )
                )
        try:
            branch_commit = client.commit_sha(repository, adapter["adapter_branch"])
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} adapter branch", exc)
        else:
            if branch_commit != current["commit"]:
                findings.append(
                    finding(
                        "REVIEW_REQUIRED",
                        "adapter-branch-advanced",
                        name,
                        "The compatibility branch differs from the published package pin.",
                        expected=current["commit"],
                        actual=branch_commit,
                    )
                )

        _check_invariants(
            client,
            findings,
            name=name,
            repository=repository,
            commit=current["commit"],
            plugin_root=current["plugin_root"],
            invariants=adapter["invariants"],
        )

        upstream = adapter["upstream"]
        upstream_repository = upstream["repository"]
        try:
            baseline_inventory = client.file_inventory(
                upstream_repository,
                upstream["baseline_commit"],
                upstream["source_path"],
            )
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} upstream baseline", exc)
        else:
            _compare_inventory(
                findings,
                subject=f"{name} upstream baseline",
                expected=upstream,
                actual=baseline_inventory,
                severity="INTEGRITY_FAILURE",
            )
        for path, expected_digest in upstream["watched_files"].items():
            try:
                data = client.read_file(
                    upstream_repository, path, upstream["baseline_commit"]
                )
            except MonitorError as exc:
                _source_unavailable(findings, f"{name} locked upstream {path}", exc)
            else:
                actual_digest = sha256_bytes(data)
                if actual_digest != expected_digest:
                    findings.append(
                        finding(
                            "INTEGRITY_FAILURE",
                            "locked-upstream-file-mismatch",
                            f"{name}:{path}",
                            "The locked upstream commit does not match its reviewed hash.",
                            expected=expected_digest,
                            actual=actual_digest,
                        )
                    )
        try:
            upstream_head = client.commit_sha(
                upstream_repository, upstream["default_branch"]
            )
        except MonitorError as exc:
            _source_unavailable(findings, f"{name} upstream head", exc)
            upstream_head = None
        if upstream_head is not None:
            if upstream_head != upstream["baseline_commit"]:
                findings.append(
                    finding(
                        "INFO",
                        "upstream-head-advanced",
                        name,
                        "The upstream branch advanced; watched content determines whether review is required.",
                        expected=upstream["baseline_commit"],
                        actual=upstream_head,
                    )
                )
            try:
                head_inventory = client.file_inventory(
                    upstream_repository, upstream_head, upstream["source_path"]
                )
            except MonitorError as exc:
                _source_unavailable(findings, f"{name} upstream current tree", exc)
            else:
                _compare_inventory(
                    findings,
                    subject=f"{name} upstream current tree",
                    expected=upstream,
                    actual=head_inventory,
                    severity="REVIEW_REQUIRED",
                )
            for path, expected_digest in upstream["watched_files"].items():
                try:
                    data = client.read_file(upstream_repository, path, upstream_head)
                except MonitorError as exc:
                    _source_unavailable(findings, f"{name} current upstream {path}", exc)
                else:
                    actual_digest = sha256_bytes(data)
                    if actual_digest != expected_digest:
                        findings.append(
                            finding(
                                "REVIEW_REQUIRED",
                                "upstream-watched-file-changed",
                                f"{name}:{path}",
                                "A reviewed upstream policy, license, or evidence file changed.",
                                expected=expected_digest,
                                actual=actual_digest,
                            )
                        )

        workflow = adapter.get("safety_checks", {}).get("disabled_sync_workflow")
        if workflow is not None:
            try:
                remote_workflow = client.get_json(
                    f"/repos/{slug}/actions/workflows/{workflow['id']}"
                )
            except MonitorError as exc:
                _source_unavailable(findings, f"{name} disabled sync workflow", exc)
            else:
                for field, expected in (
                    ("id", workflow["id"]),
                    ("path", workflow["path"]),
                    ("state", workflow["expected_state"]),
                ):
                    actual = remote_workflow.get(field) if isinstance(remote_workflow, dict) else None
                    if actual != expected:
                        findings.append(
                            finding(
                                "INTEGRITY_FAILURE",
                                "unsafe-sync-workflow-state",
                                name,
                                f"The fork-side write-back workflow has unexpected {field}.",
                                expected=expected,
                                actual=actual or "missing",
                            )
                        )

    return build_report("codex-skill-adapters", findings)


def main() -> int:
    parser = UsageArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL"))
    args = parser.parse_args()
    try:
        client = GitHubClient(api_url=args.api_url)
        report = run_monitor(args.repo_root.resolve(), client)
        write_report(report, args.output)
        return int(report["exit_code"])
    except (KeyError, TypeError, ValueError, MonitorError, OSError) as exc:
        report = build_report(
            "codex-skill-adapters",
            [finding("CONFIG_OR_USAGE_ERROR", "invalid-configuration", "monitor", str(exc))],
        )
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        report = build_report(
            "codex-skill-adapters",
            [
                finding(
                    "INTERNAL_ERROR",
                    "unexpected-internal-error",
                    "monitor",
                    f"Unexpected internal failure ({type(exc).__name__}); details suppressed.",
                )
            ],
        )
    write_failure_report(report, args.output)
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
