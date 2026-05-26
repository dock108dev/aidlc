"""Shared helpers for classifying and displaying run outcomes."""

from __future__ import annotations

from pathlib import Path

from .models import RunState, RunStatus

UNRESOLVED_ISSUE_STATUSES = frozenset({"pending", "in_progress", "blocked", "failed"})

COMPLETE_STATUSES = frozenset(
    {
        RunStatus.COMPLETE,
        RunStatus.COMPLETE_CLEAN,
        RunStatus.COMPLETE_WITH_RECOVERED_FAILURES,
        RunStatus.COMPLETE_VALIDATION_SKIPPED,
        RunStatus.COMPLETE_VALIDATION_FAILED_ALLOWED,
        RunStatus.COMPLETE_WITH_BLOCKED_ISSUES,
    }
)
COMPLETE_STATUS_VALUES = frozenset(status.value for status in COMPLETE_STATUSES)


def unresolved_issue_count(state: RunState) -> int:
    """Return issues that still require human or agent follow-up."""
    return sum(1 for issue in state.issues if issue.get("status") in UNRESOLVED_ISSUE_STATUSES)


def issue_retry_count(state: RunState) -> int:
    """Return retry attempts above each issue's first implementation attempt."""
    return sum(max(0, int(issue.get("attempt_count", 0) or 0) - 1) for issue in state.issues)


def completion_status(state: RunState) -> RunStatus:
    """Classify a finished run with the most specific complete status available."""
    if unresolved_issue_count(state) > 0:
        return RunStatus.COMPLETE_WITH_BLOCKED_ISSUES

    validation_status = (state.validation_status or "not_run").lower()
    if validation_status == "skipped":
        return RunStatus.COMPLETE_VALIDATION_SKIPPED
    if validation_status in {"failed", "incomplete"}:
        return RunStatus.COMPLETE_VALIDATION_FAILED_ALLOWED

    had_recovered_failures = (
        state.claude_calls_failed > 0
        or issue_retry_count(state) > 0
        or state.validation_issues_created > 0
        or any(
            isinstance(result, dict) and not result.get("passed", False)
            for result in state.validation_test_results[:-1]
        )
    )
    if had_recovered_failures:
        return RunStatus.COMPLETE_WITH_RECOVERED_FAILURES
    return RunStatus.COMPLETE_CLEAN


def provider_mix(state: RunState) -> str:
    """Return a compact provider/model label for reports."""
    providers = sorted(state.provider_account_usage) if state.provider_account_usage else []
    models = (
        sorted(
            str(model)
            for model, metrics in state.claude_model_usage.items()
            if isinstance(metrics, dict)
        )
        if state.claude_model_usage
        else []
    )
    provider_label = "+".join(providers) if providers else "unknown"
    model_label = "+".join(models) if models else "unknown"
    return f"{provider_label}/{model_label}"


def validation_label(state: RunState) -> str:
    """Return the validation status with its explanatory message when present."""
    status = (state.validation_status or "not_run").replace("_", " ")
    if state.validation_message:
        return f"{status} — {state.validation_message}"
    return status


def summary_validation_label(state: RunState) -> str:
    """Infer validation status for both current and legacy state files."""
    status = state.validation_status or "not_run"
    if status == "not_run" and state.validation_test_results:
        last = state.validation_test_results[-1]
        if isinstance(last, dict):
            return "passed" if last.get("passed") else "failed"
    if status == "not_run" and state.validation_cycles > 0:
        return "recorded"
    return status


def next_action(state: RunState) -> str:
    """Return a human-facing follow-up recommendation for run reports."""
    status = state.status.value
    if status == "complete_with_blocked_issues":
        return "Resolve blocked or failed issues before starting the next run."
    if status == "complete_validation_skipped":
        return "Configure `run_tests_command` or `build_validation_command` before treating this as green."
    if status == "complete_validation_failed_allowed":
        return "Inspect validation failures before starting the next run, or rerun with strict validation."
    if status == "complete_with_recovered_failures":
        return "Review recovered failures, then rerun validation if this gates a release."
    if status == "complete_clean":
        return "No follow-up required from AIDLC."
    if status in {"paused", "interrupted", "abandoned"}:
        return "Run `aidlc run --resume` when ready."
    return "Review the report details."


def summary_next_action(state: RunState) -> str:
    """Return compact follow-up text for `aidlc summarize-runs`."""
    unresolved = unresolved_issue_count(state)
    validation = summary_validation_label(state)
    if unresolved:
        return f"resolve {unresolved} issue(s)"
    if validation in {"failed", "incomplete"}:
        return "fix validation"
    if validation == "skipped":
        return "configure validation"
    if state.claude_calls_failed:
        return "review recovered failures"
    return "-"


def run_project_label(state: RunState) -> str:
    """Return a short project label from the run state's project root."""
    root = Path(state.project_root) if state.project_root else Path("?")
    return root.name
