#!/usr/bin/env python3
"""Report Agent Plugins and Agent Skills standard drift without adopting it."""

from __future__ import annotations

import argparse
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
from monitor_lib import sha256_bytes
from monitor_lib import write_report
from monitor_lib import write_failure_report


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def semver_key(value: str) -> tuple[int, int, int, int, tuple[tuple[int, object], ...]]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise MonitorError(f"invalid locked semantic version {value!r}")
    prerelease = match.group(4)
    identifiers: list[tuple[int, object]] = []
    if prerelease:
        for part in prerelease.split("."):
            if part.isdigit():
                identifiers.append((0, int(part)))
            else:
                identifiers.append((1, part))
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        1 if prerelease is None else 0,
        tuple(identifiers),
    )


def _tracked_agent_plugin_files(lock: dict[str, Any]) -> list[tuple[str, str, str]]:
    tracked = [
        (
            "specification",
            lock["specification"]["path"],
            lock["specification"]["sha256"],
        ),
        (
            "specification-license",
            lock["specification_license"]["path"],
            lock["specification_license"]["sha256"],
        ),
        (
            "schemas-license",
            lock["schemas_license"]["path"],
            lock["schemas_license"]["sha256"],
        ),
    ]
    for name, schema in sorted(lock["schemas"].items()):
        tracked.append((f"{name}-schema", schema["upstream_path"], schema["sha256"]))
    return tracked


def _check_remote_hash(
    client: GitHubClient,
    *,
    repository: str,
    ref: str,
    path: str,
    expected: str,
    subject: str,
    mismatch_severity: str,
    mismatch_code: str,
) -> tuple[list[Any], str | None]:
    try:
        actual = sha256_bytes(client.read_file(repository, path, ref))
    except MonitorError as exc:
        return [finding("SOURCE_UNAVAILABLE", "remote-read-failed", subject, str(exc))], None
    if actual != expected:
        return [
            finding(
                mismatch_severity,
                mismatch_code,
                subject,
                f"Remote bytes at {path} do not match the reviewed lock.",
                expected=expected,
                actual=actual,
            )
        ], actual
    return [], actual


def run_monitor(repo_root: Path, client: GitHubClient) -> dict[str, Any]:
    findings = []
    try:
        lock = load_json(repo_root / "standards.lock.json")
        if lock.get("format_version") != 1:
            raise MonitorError("standards.lock.json format_version must be 1")
        agent_plugins = lock["agent_plugins"]
        agent_skills = lock["agent_skills"]
    except (KeyError, TypeError, MonitorError) as exc:
        return build_report(
            "agent-plugin-standards",
            [finding("CONFIG_OR_USAGE_ERROR", "invalid-lock", "standards.lock.json", str(exc))],
        )

    local_locks = [
        (
            "Agent Plugins specification",
            agent_plugins["specification"]["vendored_path"],
            agent_plugins["specification"]["sha256"],
        ),
        (
            "Agent Plugins specification license",
            agent_plugins["specification_license"]["vendored_path"],
            agent_plugins["specification_license"]["sha256"],
        ),
        (
            "Agent Plugins schema license",
            agent_plugins["schemas_license"]["vendored_path"],
            agent_plugins["schemas_license"]["sha256"],
        ),
        (
            "Agent Skills specification",
            agent_skills["specification"]["vendored_path"],
            agent_skills["specification"]["sha256"],
        ),
        (
            "Agent Skills license",
            agent_skills["license"]["vendored_path"],
            agent_skills["license"]["sha256"],
        ),
    ]
    local_locks.extend(
        (
            f"Agent Plugins {name} schema",
            schema["path"],
            schema["sha256"],
        )
        for name, schema in sorted(agent_plugins["schemas"].items())
    )
    for subject, relative, expected in local_locks:
        path = repo_root / relative
        try:
            actual = sha256_bytes(path.read_bytes())
        except OSError as exc:
            findings.append(
                finding(
                    "INTEGRITY_FAILURE",
                    "vendored-file-missing",
                    subject,
                    str(exc),
                )
            )
            continue
        if actual != expected:
            findings.append(
                finding(
                    "INTEGRITY_FAILURE",
                    "vendored-hash-mismatch",
                    subject,
                    "Local vendored bytes do not match standards.lock.json.",
                    expected=expected,
                    actual=actual,
                )
            )

    plugin_repository = agent_plugins.get("repository")
    plugin_branch = agent_plugins.get("default_branch")
    plugin_commit = agent_plugins.get("commit")
    plugin_version = agent_plugins.get("version")
    discovery = agent_plugins.get("release_discovery")
    if not all(
        isinstance(value, str)
        for value in (plugin_repository, plugin_branch, plugin_commit, plugin_version)
    ) or not isinstance(discovery, dict):
        findings.append(
            finding(
                "CONFIG_OR_USAGE_ERROR",
                "invalid-lock",
                "Agent Plugins",
                "Repository, branch, commit, version, or release discovery is malformed.",
            )
        )
    else:
        tracked = _tracked_agent_plugin_files(agent_plugins)
        for label, path, expected in tracked:
            new, _ = _check_remote_hash(
                client,
                repository=plugin_repository,
                ref=plugin_commit,
                path=path,
                expected=expected,
                subject=f"Agent Plugins locked {label}",
                mismatch_severity="INTEGRITY_FAILURE",
                mismatch_code="locked-baseline-mismatch",
            )
            findings.extend(new)
        try:
            current_head = client.commit_sha(plugin_repository, plugin_branch)
        except MonitorError as exc:
            findings.append(
                finding("SOURCE_UNAVAILABLE", "remote-read-failed", "Agent Plugins head", str(exc))
            )
            current_head = None
        if current_head is not None:
            if current_head != plugin_commit:
                findings.append(
                    finding(
                        "INFO",
                        "upstream-head-advanced",
                        "Agent Plugins",
                        "The default branch advanced; tracked release files determine whether action is required.",
                        expected=plugin_commit,
                        actual=current_head,
                    )
                )
            for label, path, expected in tracked:
                new, _ = _check_remote_hash(
                    client,
                    repository=plugin_repository,
                    ref=current_head,
                    path=path,
                    expected=expected,
                    subject=f"Agent Plugins current {label}",
                    mismatch_severity="REVIEW_REQUIRED",
                    mismatch_code="same-version-mutation",
                )
                findings.extend(new)
        if discovery.get("strategy") != "semver-files":
            findings.append(
                finding(
                    "CONFIG_OR_USAGE_ERROR",
                    "invalid-lock",
                    "Agent Plugins release discovery",
                    "Only semver-files discovery is supported.",
                )
            )
        else:
            path = discovery.get("path")
            suffix = discovery.get("suffix")
            if not isinstance(path, str) or not isinstance(suffix, str) or not suffix:
                findings.append(
                    finding(
                        "CONFIG_OR_USAGE_ERROR",
                        "invalid-lock",
                        "Agent Plugins release discovery",
                        "Discovery path and suffix must be non-empty strings.",
                    )
                )
            else:
                try:
                    locked_key = semver_key(plugin_version)
                    entries = client.directory_entries(plugin_repository, path, plugin_branch)
                    discovered: list[str] = []
                    for entry in entries:
                        name = entry.get("name")
                        if (
                            entry.get("type") == "file"
                            and isinstance(name, str)
                            and name.endswith(suffix)
                        ):
                            candidate = name[: -len(suffix)]
                            if SEMVER_RE.fullmatch(candidate):
                                discovered.append(candidate)
                    newer = sorted(
                        (value for value in discovered if semver_key(value) > locked_key),
                        key=semver_key,
                    )
                    if newer:
                        findings.append(
                            finding(
                                "REVIEW_REQUIRED",
                                "new-standard-version",
                                "Agent Plugins",
                                "A newer published specification file is present.",
                                expected=plugin_version,
                                actual=", ".join(newer),
                            )
                        )
                except MonitorError as exc:
                    findings.append(
                        finding(
                            "SOURCE_UNAVAILABLE",
                            "release-discovery-failed",
                            "Agent Plugins",
                            str(exc),
                        )
                    )

    skills_repository = agent_skills.get("repository")
    skills_branch = agent_skills.get("default_branch")
    skills_commit = agent_skills.get("commit")
    skills_discovery = agent_skills.get("release_discovery")
    if not all(
        isinstance(value, str)
        for value in (skills_repository, skills_branch, skills_commit)
    ) or not isinstance(skills_discovery, dict):
        findings.append(
            finding(
                "CONFIG_OR_USAGE_ERROR",
                "invalid-lock",
                "Agent Skills",
                "Repository, branch, commit, or release discovery is malformed.",
            )
        )
    else:
        tracked_skills = [
            (
                "specification",
                agent_skills["specification"]["path"],
                agent_skills["specification"]["sha256"],
            ),
            (
                "license",
                agent_skills["license"]["path"],
                agent_skills["license"]["sha256"],
            ),
        ]
        for label, path, expected in tracked_skills:
            new, _ = _check_remote_hash(
                client,
                repository=skills_repository,
                ref=skills_commit,
                path=path,
                expected=expected,
                subject=f"Agent Skills locked {label}",
                mismatch_severity="INTEGRITY_FAILURE",
                mismatch_code="locked-baseline-mismatch",
            )
            findings.extend(new)
        try:
            current_head = client.commit_sha(skills_repository, skills_branch)
        except MonitorError as exc:
            findings.append(
                finding("SOURCE_UNAVAILABLE", "remote-read-failed", "Agent Skills head", str(exc))
            )
            current_head = None
        if current_head is not None:
            if current_head != skills_commit:
                findings.append(
                    finding(
                        "INFO",
                        "upstream-head-advanced",
                        "Agent Skills",
                        "The unversioned specification repository advanced.",
                        expected=skills_commit,
                        actual=current_head,
                    )
                )
            for label, path, expected in tracked_skills:
                new, _ = _check_remote_hash(
                    client,
                    repository=skills_repository,
                    ref=current_head,
                    path=path,
                    expected=expected,
                    subject=f"Agent Skills current {label}",
                    mismatch_severity="REVIEW_REQUIRED",
                    mismatch_code="unversioned-standard-changed",
                )
                findings.extend(new)
        if skills_discovery != {
            "strategy": "unversioned-file",
            "path": agent_skills.get("upstream_path"),
        }:
            findings.append(
                finding(
                    "CONFIG_OR_USAGE_ERROR",
                    "invalid-lock",
                    "Agent Skills release discovery",
                    "The unversioned tracked-file discovery rule is malformed.",
                )
            )

    return build_report("agent-plugin-standards", findings)


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
            "agent-plugin-standards",
            [finding("CONFIG_OR_USAGE_ERROR", "invalid-configuration", "monitor", str(exc))],
        )
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        report = build_report(
            "agent-plugin-standards",
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
