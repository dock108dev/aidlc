"""Report generation for AIDLC runs."""

from datetime import datetime, timezone
from pathlib import Path

from .models import RunState, IssueStatus, Issue


def generate_run_report(state: RunState, report_dir: Path) -> Path:
    report_path = report_dir / f"run_report_{state.run_id}.md"

    plan_h = state.plan_elapsed_seconds / 3600
    plan_budget_h = state.plan_budget_seconds / 3600
    elapsed_h = state.elapsed_seconds / 3600
    wall_h = state.wall_clock_seconds / 3600

    lines = [
        f"# AIDLC Run Report: {state.run_id}\n",
        f"**Status**: {state.status.value}",
        f"**Phase**: {state.phase.value}",
        f"**Project**: {state.project_root}",
        f"**Started**: {state.started_at or 'N/A'}",
        f"**Last Updated**: {state.last_updated}",
        f"**Planning time**: {plan_h:.1f}h / {plan_budget_h:.0f}h budget",
        f"**Total elapsed (Claude)**: {elapsed_h:.1f}h",
        f"**Total elapsed (wall)**: {wall_h:.1f}h",
        f"**Stop Reason**: {state.stop_reason or 'N/A'}",
        "",
    ]

    # Audit summary (if audit was run)
    if state.audit_depth != "none":
        lines.extend([
            "## Audit Summary",
            "",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Depth | {state.audit_depth} |",
            f"| Completed | {state.audit_completed} |",
            f"| Conflicts | {len(state.audit_conflicts)} |",
            "",
        ])

    lines.extend([
        "## Planning Summary",
        "",
        f"| Metric | Count |",
        f"|---|---|",
        f"| Docs scanned | {state.docs_scanned} |",
        f"| Planning cycles | {state.planning_cycles} |",
        f"| Issues created | {state.issues_created} |",
        f"| Files created | {state.files_created} |",
        "",
        "## Implementation Summary",
        "",
        f"| Metric | Count |",
        f"|---|---|",
        f"| Total issues | {state.total_issues} |",
        f"| Implementation cycles | {state.implementation_cycles} |",
        f"| Issues implemented | {state.issues_implemented} |",
        f"| Issues verified | {state.issues_verified} |",
        f"| Issues failed | {state.issues_failed} |",
        "",
    ])

    # Issue breakdown
    if state.issues:
        lines.append("## Issues\n")
        lines.append("| ID | Title | Status | Attempts |")
        lines.append("|---|---|---|---|")
        for d in state.issues:
            issue = Issue.from_dict(d)
            lines.append(
                f"| {issue.id} | {issue.title} | {issue.status.value} | {issue.attempt_count} |"
            )
        lines.append("")

    # Artifacts
    if state.created_artifacts:
        lines.append("## Created Artifacts\n")
        for a in state.created_artifacts:
            if isinstance(a, dict):
                lines.append(f"- [{a.get('action', '?')}] {a.get('path', '?')} ({a.get('type', '?')})")
            else:
                lines.append(f"- {a}")
        lines.append("")

    if state.notes:
        lines.append(f"## Notes\n\n{state.notes}\n")

    content = "\n".join(lines)
    report_path.write_text(content)
    return report_path


def generate_checkpoint_summary(state: RunState, report_dir: Path) -> Path:
    cp_path = report_dir / f"checkpoint_{state.checkpoint_count:04d}.md"
    elapsed = state.elapsed_seconds / 3600

    content = f"""# Checkpoint {state.checkpoint_count}

- **Time**: {datetime.now(timezone.utc).isoformat()}
- **Phase**: {state.phase.value}
- **Elapsed**: {elapsed:.1f}h
- **Planning cycles**: {state.planning_cycles}
- **Issues created**: {state.issues_created}
- **Implementation cycles**: {state.implementation_cycles}
- **Issues implemented**: {state.issues_implemented}
- **Current issue**: {state.current_issue_id or 'none'}
"""
    cp_path.write_text(content)
    return cp_path
