#!/usr/bin/env python3
"""Deduplicate a drift report into one GitHub issue, or close it when clean."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from monitor_lib import GitHubClient
from monitor_lib import MonitorError
from monitor_lib import UsageArgumentParser
from monitor_lib import STATUS_EXIT_CODES
from monitor_lib import build_report
from monitor_lib import finding
from monitor_lib import load_json
from monitor_lib import write_report


MONITOR_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MONITOR_IDS = {
    "agent-plugin-standards": "agent-standards",
    "codex-skill-adapters": "reviewed-adapters",
}
MAX_ISSUE_BODY_CHARS = 60_000
LABELS = {
    "automated-drift": ("B60205", "Read-only automation detected drift"),
    "monitor:agent-standards": ("5319E7", "Agent plugin standards monitor"),
    "monitor:reviewed-adapters": ("1D76DB", "Reviewed skill adapters monitor"),
}


def _validated_report(path: Path) -> dict[str, Any]:
    report = load_json(path)
    required = {
        "schema_version",
        "monitor",
        "status",
        "exit_code",
        "summary",
        "fingerprint",
        "checked_at",
        "findings",
    }
    if set(report) != required or report.get("schema_version") != 1:
        raise MonitorError("report has an unsupported schema")
    monitor = report.get("monitor")
    status = report.get("status")
    fingerprint = report.get("fingerprint")
    if not isinstance(monitor, str) or MONITOR_RE.fullmatch(monitor) is None:
        raise MonitorError("report monitor name is invalid")
    if monitor not in MONITOR_IDS:
        raise MonitorError("report monitor is not managed by this reconciler")
    if status not in STATUS_EXIT_CODES or status == "ALERT_FAILURE":
        raise MonitorError("report status is invalid for reconciliation")
    if report.get("exit_code") != STATUS_EXIT_CODES[status]:
        raise MonitorError("report status and exit_code disagree")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise MonitorError("report fingerprint is malformed")
    if not isinstance(report.get("findings"), list):
        raise MonitorError("report findings must be an array")
    return report


def _monitor_id(report: dict[str, Any]) -> str:
    return MONITOR_IDS[report["monitor"]]


def _marker(report: dict[str, Any]) -> str:
    return (
        f"<!-- codex-skills-monitor id={_monitor_id(report)} "
        f"fingerprint={report['fingerprint']} -->"
    )


def _managed_marker_pattern(monitor_id: str) -> re.Pattern[str]:
    return re.compile(
        rf"<!-- codex-skills-monitor id={re.escape(monitor_id)} "
        r"fingerprint=([0-9a-f]{64}) -->"
    )


def _escape_markdown(value: Any, *, limit: int = 2_000) -> str:
    text = str(value)
    text = "".join(character for character in text if character in "\n\t" or ord(character) >= 32)
    text = html.escape(text, quote=False)
    text = text[:limit]
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def _title(report: dict[str, Any]) -> str:
    return f"[monitor][{_monitor_id(report)}] Review required"


def _body(report: dict[str, Any]) -> str:
    lines = [
        _marker(report),
        f"# {_escape_markdown(report['status'])}",
        "",
        _escape_markdown(report["summary"]),
        "",
        f"Checked at: `{report['checked_at']}`",
        f"Exit code: `{report['exit_code']}`",
        "",
        "## Findings",
        "",
    ]
    actionable = [item for item in report["findings"] if item.get("severity") != "INFO"]
    if not actionable:
        lines.append("No actionable findings.")
    for item in actionable:
        lines.extend(
            [
                f"- **{_escape_markdown(item.get('severity', 'UNKNOWN'))} · {_escape_markdown(item.get('code', 'unknown'))} · {_escape_markdown(item.get('subject', 'unknown'))}**",
                f"  {_escape_markdown(item.get('detail', ''))}",
            ]
        )
        if item.get("expected") is not None:
            lines.append(f"  Expected: {_escape_markdown(item['expected'])}")
        if item.get("actual") is not None:
            lines.append(f"  Actual: {_escape_markdown(item['actual'])}")
    lines.extend(
        [
            "",
            "This monitor is read-only. Review and update locks or package pins in a separate change.",
        ]
    )
    rendered = "\n".join(lines) + "\n"
    if len(rendered) > MAX_ISSUE_BODY_CHARS:
        rendered = rendered[: MAX_ISSUE_BODY_CHARS - 80] + "\n\n_Report truncated at the safety limit._\n"
    return rendered


def _ensure_labels(
    client: GitHubClient, repository: str, names: list[str]
) -> None:
    labels = client.get_json(
        f"/repos/{repository}/labels", query={"per_page": 100}
    )
    if not isinstance(labels, list):
        raise MonitorError("GitHub labels response is malformed")
    existing = {
        item.get("name")
        for item in labels
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for name in names:
        if name in existing:
            continue
        color, description = LABELS[name]
        client.request_json(
            "POST",
            f"/repos/{repository}/labels",
            body={"name": name, "color": color, "description": description},
        )


def reconcile(report: dict[str, Any], client: GitHubClient, repository: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise MonitorError("GITHUB_REPOSITORY must be owner/name")
    issues: list[Any] = []
    for page in range(1, 4):
        batch = client.get_json(
            f"/repos/{repository}/issues",
            query={
                "state": "all",
                "per_page": 100,
                "page": page,
                "sort": "updated",
                "direction": "desc",
            },
        )
        if not isinstance(batch, list):
            raise MonitorError("GitHub issues response is malformed")
        issues.extend(batch)
        if len(batch) < 100:
            break
    monitor_id = _monitor_id(report)
    marker_pattern = _managed_marker_pattern(monitor_id)
    managed = sorted(
        (
            issue
            for issue in issues
            if isinstance(issue, dict)
            and "pull_request" not in issue
            and isinstance(issue.get("body"), str)
            and marker_pattern.search(issue["body"])
            and isinstance(issue.get("number"), int)
        ),
        key=lambda issue: issue["number"],
    )
    if report["status"] == "CLEAN":
        for issue in managed:
            if issue.get("state") == "closed":
                continue
            client.request_json(
                "POST",
                f"/repos/{repository}/issues/{issue['number']}/comments",
                body={
                    "body": (
                        f"Recovered at `{report['checked_at']}`. "
                        f"Current fingerprint: `{report['fingerprint']}`."
                    )
                },
            )
            client.request_json(
                "PATCH",
                f"/repos/{repository}/issues/{issue['number']}",
                body={"state": "closed", "state_reason": "completed"},
            )
        return

    body = _body(report)
    label_names = ["automated-drift", f"monitor:{monitor_id}"]
    _ensure_labels(client, repository, label_names)
    if not managed:
        client.request_json(
            "POST",
            f"/repos/{repository}/issues",
            body={"title": _title(report), "body": body, "labels": label_names},
        )
        return
    canonical = managed[0]
    old_match = marker_pattern.search(canonical["body"])
    old_fingerprint = old_match.group(1) if old_match else None
    changed = old_fingerprint != report["fingerprint"]
    if changed or canonical.get("state") != "open":
        client.request_json(
            "PATCH",
            f"/repos/{repository}/issues/{canonical['number']}",
            body={
                "title": _title(report),
                "body": body,
                "labels": label_names,
                "state": "open",
            },
        )
    if changed:
        client.request_json(
            "POST",
            f"/repos/{repository}/issues/{canonical['number']}/comments",
            body={
                "body": (
                    "Monitor findings changed: "
                    f"`{old_fingerprint[:12] if old_fingerprint else 'none'}` → "
                    f"`{report['fingerprint'][:12]}`."
                )
            },
        )
    for duplicate in managed[1:]:
        if duplicate.get("state") == "closed":
            continue
        client.request_json(
            "PATCH",
            f"/repos/{repository}/issues/{duplicate['number']}",
            body={"state": "closed", "state_reason": "not_planned"},
        )


def main() -> int:
    parser = UsageArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL"))
    args = parser.parse_args()
    if not args.repository:
        report = build_report(
            "issue-reconciliation",
            [
                finding(
                    "CONFIG_OR_USAGE_ERROR",
                    "missing-repository",
                    "issue reconciliation",
                    "GITHUB_REPOSITORY or --repository is required.",
                )
            ],
        )
        write_report(report, None)
        return int(report["exit_code"])
    try:
        source_report = _validated_report(args.report)
    except MonitorError as exc:
        report = build_report(
            "issue-reconciliation",
            [finding("CONFIG_OR_USAGE_ERROR", "invalid-report", "issue reconciliation", str(exc))],
        )
        write_report(report, None)
        return int(report["exit_code"])
    try:
        reconcile(source_report, GitHubClient(api_url=args.api_url), args.repository)
    except MonitorError as exc:
        report = build_report(
            "issue-reconciliation",
            [finding("ALERT_FAILURE", "issue-reconciliation-failed", "GitHub issue", str(exc))],
        )
        write_report(report, None)
        return int(report["exit_code"])
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        report = build_report(
            "issue-reconciliation",
            [
                finding(
                    "INTERNAL_ERROR",
                    "unexpected-internal-error",
                    "issue reconciliation",
                    f"Unexpected internal failure ({type(exc).__name__}); details suppressed.",
                )
            ],
        )
        write_report(report, None)
        return int(report["exit_code"])
    print(json.dumps({"status": "CLEAN", "monitor": source_report["monitor"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
