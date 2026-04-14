"""Report generation for AIDLC runs."""

from datetime import datetime, timezone
from pathlib import Path

from .models import RunState, Issue


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
            "| Metric | Value |",
            "|---|---|",
            f"| Depth | {state.audit_depth} |",
            f"| Completed | {state.audit_completed} |",
            f"| Conflicts | {len(state.audit_conflicts)} |",
            "",
        ])

    lines.extend([
        "## Planning Summary",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Docs scanned | {state.docs_scanned} |",
        f"| Planning cycles | {state.planning_cycles} |",
        f"| Issues created | {state.issues_created} |",
        f"| Files created | {state.files_created} |",
        "",
        "## Implementation Summary",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Total issues | {state.total_issues} |",
        f"| Implementation cycles | {state.implementation_cycles} |",
        f"| Issues implemented | {state.issues_implemented} |",
        f"| Issues verified | {state.issues_verified} |",
        f"| Issues failed | {state.issues_failed} |",
        "",
    ])

    lines.extend([
        "## Claude Telemetry",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Calls total | {state.claude_calls_total} |",
        f"| Calls succeeded | {state.claude_calls_succeeded} |",
        f"| Calls failed | {state.claude_calls_failed} |",
        f"| Retries | {state.claude_retries_total} |",
        f"| Input tokens | {state.claude_input_tokens} |",
        f"| Output tokens | {state.claude_output_tokens} |",
        f"| Cache write tokens | {state.claude_cache_creation_input_tokens} |",
        f"| Cache read tokens | {state.claude_cache_read_input_tokens} |",
        f"| Total input tokens | {state.claude_total_input_tokens} |",
        f"| Total tokens | {state.claude_total_tokens} |",
        f"| Web search requests | {state.claude_web_search_requests} |",
        f"| Web fetch requests | {state.claude_web_fetch_requests} |",
        f"| Cost exact (USD) | {state.claude_cost_usd_exact:.4f} |",
        f"| Cost estimated (USD) | {state.claude_cost_usd_estimated:.4f} |",
        f"| Exact-cost calls | {state.claude_exact_cost_calls} |",
        f"| Estimated-cost calls | {state.claude_estimated_cost_calls} |",
        "",
    ])

    if state.claude_model_usage:
        lines.append("### Claude Model Breakdown\n")
        lines.append("| Model | Calls | In | Out | Cache Write | Cache Read | Cost Exact (USD) | Cost Est (USD) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for model, metrics in sorted(state.claude_model_usage.items()):
            if not isinstance(metrics, dict):
                continue
            lines.append(
                f"| {model} | {metrics.get('calls', 0)} | "
                f"{metrics.get('input_tokens', 0)} | {metrics.get('output_tokens', 0)} | "
                f"{metrics.get('cache_creation_input_tokens', 0)} | {metrics.get('cache_read_input_tokens', 0)} | "
                f"{float(metrics.get('cost_usd_exact', 0.0) or 0.0):.4f} | "
                f"{float(metrics.get('cost_usd_estimated', 0.0) or 0.0):.4f} |"
            )
        lines.append("")

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

    # Validation
    if state.validation_cycles > 0:
        lines.append("## Validation Summary\n")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Validation cycles | {state.validation_cycles} |")
        lines.append(f"| Fix issues created | {state.validation_issues_created} |")
        for result in state.validation_test_results:
            cycle = result.get("cycle", "?")
            passed = result.get("passed", False)
            failures = result.get("failure_count", 0)
            status = "PASSED" if passed else f"FAILED ({failures} failures)"
            lines.append(f"| Cycle {cycle} | {status} |")
        lines.append("")

    # Finalization
    if state.finalize_passes_completed:
        lines.append("## Finalization Summary\n")
        lines.append("| Pass | Status |")
        lines.append("|------|--------|")
        for p in state.finalize_passes_requested:
            status = "complete" if p in state.finalize_passes_completed else "skipped"
            lines.append(f"| {p} | {status} |")
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
- **Claude calls**: {state.claude_calls_total} total ({state.claude_calls_succeeded} ok, {state.claude_calls_failed} failed, {state.claude_retries_total} retries)
- **Claude tokens**: in={state.claude_input_tokens}, out={state.claude_output_tokens}, cache_write={state.claude_cache_creation_input_tokens}, cache_read={state.claude_cache_read_input_tokens}, total={state.claude_total_tokens}
- **Claude tool requests**: web_search={state.claude_web_search_requests}, web_fetch={state.claude_web_fetch_requests}
- **Claude cost (USD)**: exact={state.claude_cost_usd_exact:.4f}, estimated={state.claude_cost_usd_estimated:.4f}
"""
    cp_path.write_text(content)
    return cp_path
