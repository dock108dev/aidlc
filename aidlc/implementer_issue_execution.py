"""Single-issue implementation execution helpers."""

from __future__ import annotations

import sys
import time

from ._proc import run_with_group_kill
from .implementer_signals import (
    SERVICE_OUTAGE_STOP_REASON,
    compact_error_text,
    is_all_models_token_exhausted,
    is_no_models_available,
    is_service_outage,
    sample_error_payload,
)
from .implementer_targeted_tests import effective_implementation_test_command
from .issue_model import FAILURE_CAUSE_TOKEN_EXHAUSTED, FAILURE_CAUSE_UNKNOWN
from .models import Issue, IssueStatus
from .routing.helpers import routed_model_from_result
from .schemas import ImplementationResult, parse_implementation_result
from .timing import add_console_time


def _patchable_symbol(name: str, fallback):
    implementer_module = sys.modules.get("aidlc.implementer")
    return getattr(implementer_module, name, fallback)


def implement_issue(impl, issue: Issue) -> bool:
    """Implement one issue through the routed provider, then verify its result."""
    resuming_interrupted = issue.status == IssueStatus.IN_PROGRESS
    if resuming_interrupted:
        impl.logger.info(
            f"=== Resuming interrupted {issue.id}: {issue.title} "
            f"(attempt {issue.attempt_count}/{issue.max_attempts}) ==="
        )
        impl.logger.warning(
            f"{issue.id}: previous attempt was killed mid-flight. "
            "Working tree may contain partial changes from that attempt; "
            "the model will see them and decide whether to extend or revert."
        )
    else:
        issue.attempt_count += 1
        impl.logger.info(
            f"=== Implementing {issue.id}: {issue.title} "
            f"(attempt {issue.attempt_count}/{issue.max_attempts}) ==="
        )
    issue.status = IssueStatus.IN_PROGRESS
    impl.state.current_issue_id = issue.id
    impl.state.update_issue(issue)
    impl._sync_issue_markdown(issue)

    use_impl_threading = impl.config.get("claude_implementation_cli_threading", True)
    impl._impl_continuation = (
        impl._load_or_create_impl_continuation(issue, resuming_interrupted)
        if use_impl_threading
        else {}
    )
    session_cont = impl._impl_continuation if use_impl_threading else None
    impl_model_pin: str | None = None

    prompt = impl._build_implementation_prompt(issue)
    impl.logger.info(f"  Prompt size: {len(prompt):,} chars (~{len(prompt) // 4:,} tokens)")
    is_complex = is_complex_issue(impl, issue)
    if hasattr(impl.cli, "set_complexity"):
        impl.cli.set_complexity("complex" if is_complex else "normal")

    start_time = time.time()
    result = impl.cli.execute_prompt(
        prompt,
        impl.project_root,
        allow_edits=True,
        model_override=impl_model_pin,
        session_continuation=session_cont,
    )
    if use_impl_threading and result.get("success") and result.get("provider_id") == "claude":
        delay = float(impl.config.get("claude_session_release_delay_seconds", 2.0))
        if delay > 0:
            time.sleep(delay)
    impl._log_provider_result(issue, result)
    impl.state.record_provider_result(result, impl.config, phase="implementation")
    if use_impl_threading and result.get("success"):
        model_pin = routed_model_from_result(result)
        if model_pin:
            impl_model_pin = model_pin
        _capture_provider_session_id(impl, issue, result)
    impl.state.elapsed_seconds += time.time() - start_time

    output_text = result.get("output", "")
    if output_text:
        output_dir = impl.run_dir / "provider_outputs"
        output_dir.mkdir(exist_ok=True)
        (output_dir / f"impl_{issue.id}_{issue.attempt_count:02d}.md").write_text(output_text)

    if not result["success"]:
        return _handle_provider_failure(impl, issue, result, resuming_interrupted)

    impl_result = _parse_or_synthesize_result(impl, issue, output_text)
    _run_issue_tests(impl, issue, impl_result, session_cont, impl_model_pin)
    _verify_changed_files_if_needed(impl, issue, impl_result)
    return _finalize_issue_result(impl, issue, impl_result)


def is_complex_issue(impl, issue: Issue) -> bool:
    """Return whether an issue should use the complex implementation route."""
    is_complex = False
    reasons = []

    if impl.escalate_on_retry and issue.attempt_count >= 2:
        is_complex = True
        reasons.append("retry")
    if len(issue.acceptance_criteria or []) >= impl.complexity_ac_threshold:
        is_complex = True
        reasons.append("acceptance_criteria")
    if len(issue.dependencies or []) >= impl.complexity_dep_threshold:
        is_complex = True
        reasons.append("dependencies")
    if len((issue.description or "").strip()) >= impl.complexity_description_threshold:
        is_complex = True
        reasons.append("description_size")
    if impl.complexity_labels:
        labels = {str(label).strip().lower() for label in (issue.labels or [])}
        if labels.intersection(impl.complexity_labels):
            is_complex = True
            reasons.append("labels")

    if reasons:
        impl.logger.info(
            f"{issue.id}: using implementation_complex routing (complexity: {', '.join(reasons)})"
        )
    else:
        impl.logger.info(f"{issue.id}: using standard implementation routing")
    return is_complex


def run_tests(
    impl,
    capture_output: bool = False,
    *,
    files_changed: list[str] | None = None,
    use_targeted_if_unstable: bool = True,
) -> bool | str:
    """Run the configured test command, optionally returning captured output."""
    if not impl.test_command:
        return True if not capture_output else ""
    if impl.config.get("dry_run"):
        return True if not capture_output else "[DRY RUN] Tests passed"

    cmd = impl.test_command
    if use_targeted_if_unstable:
        cmd = effective_implementation_test_command(
            impl.project_root,
            impl.test_command,
            files_changed,
            project_wide_tests_unstable=impl.state.project_wide_tests_unstable,
            config=impl.config,
        )
        if cmd != impl.test_command:
            impl.logger.info(
                f"Running tests (targeted; project-wide suite unstable): {cmd[:240]}"
                f"{'...' if len(cmd) > 240 else ''}"
            )

    t0 = time.time()
    try:
        try:
            result = _patchable_symbol("run_with_group_kill", run_with_group_kill)(
                cmd,
                cwd=str(impl.project_root),
                timeout=impl.test_timeout,
            )
        except Exception as e:
            impl.logger.error(f"Failed to run tests: {e}")
            return f"Failed to run tests: {e}" if capture_output else False
        if result.timed_out:
            impl.logger.warning(f"Test suite timed out ({impl.test_timeout}s)")
            return f"Tests timed out after {impl.test_timeout}s" if capture_output else False
        if capture_output:
            return result.stdout + result.stderr
        return result.returncode == 0
    finally:
        add_console_time(impl.state, t0)


def _capture_provider_session_id(impl, issue: Issue, result: dict) -> None:
    provider = result.get("provider_id")
    if provider not in {"openai", "claude"}:
        return
    session_id = result.get("continuation_session_id")
    if not session_id:
        return
    impl._impl_continuation[provider] = session_id
    impl._save_impl_continuation(issue, impl._impl_continuation)


def _handle_provider_failure(impl, issue: Issue, result: dict, resuming_interrupted: bool) -> bool:
    sampled_error = sample_error_payload(result.get("error"))
    compact_error = compact_error_text(sampled_error)
    impl.logger.error(f"Implementation of {issue.id} failed: {compact_error}")

    if is_service_outage(result):
        if not resuming_interrupted and issue.attempt_count > 0:
            issue.attempt_count -= 1
        issue.status = IssueStatus.PENDING
        issue.failure_cause = None
        issue.implementation_notes += "\n[outage] attempt rolled back due to Claude service outage"
        impl.state.stop_reason = SERVICE_OUTAGE_STOP_REASON
        impl.state.update_issue(issue)
        impl._sync_issue_markdown(issue)
        return False

    if is_all_models_token_exhausted(result):
        message = (
            "All available models/providers appear out of tokens or quota; "
            "stopping run to allow safe resume later."
        )
        issue.failure_cause = FAILURE_CAUSE_TOKEN_EXHAUSTED
    elif is_no_models_available(result):
        message = (
            "No models/providers are currently available; "
            "saving state and exiting for clean resume."
        )
        issue.failure_cause = FAILURE_CAUSE_TOKEN_EXHAUSTED
    else:
        message = None
        issue.failure_cause = FAILURE_CAUSE_UNKNOWN

    if message:
        impl.state.stop_reason = message
        impl.logger.error(message)
    issue.status = IssueStatus.FAILED
    issue.implementation_notes += (
        f"\nAttempt {issue.attempt_count} failed (sample):\n{sampled_error}"
    )
    impl.state.update_issue(issue)
    impl._sync_issue_markdown(issue)
    return False


def _parse_or_synthesize_result(impl, issue: Issue, output_text: str) -> ImplementationResult:
    if impl.config.get("dry_run"):
        return ImplementationResult(
            issue_id=issue.id,
            success=True,
            summary="[DRY RUN]",
            files_changed=[],
            tests_passed=True,
        )
    try:
        parser = _patchable_symbol("parse_implementation_result", parse_implementation_result)
        return parser(output_text)
    except ValueError as e:
        impl.logger.warning(f"No structured JSON result for {issue.id}: {e}")
        return _synthesize_result_from_git_diff(impl, issue, e)


def _synthesize_result_from_git_diff(impl, issue: Issue, err: ValueError) -> ImplementationResult:
    changed_files, detection_ok = impl._get_changed_files(with_status=True)
    if changed_files and detection_ok:
        impl.logger.warning(
            f"{issue.id}: accepting {len(changed_files)} file change(s) from git "
            "diff despite missing/garbled JSON envelope; tests will gate."
        )
        return ImplementationResult(
            issue_id=issue.id,
            success=True,
            summary=f"Synthesized from git diff: {len(changed_files)} file(s) changed",
            files_changed=changed_files,
            tests_passed=False,
            notes=f"JSON parse failed ({err}); accepted via git-diff verification",
        )
    if changed_files and not detection_ok:
        impl.logger.error(
            f"FAIL: {issue.id} - file change detection unavailable; cannot safely "
            "accept unstructured output."
        )
        summary = "Unstructured result with unavailable change detection"
        notes = "Change detection unavailable"
    else:
        impl.logger.error(
            f"FAIL: {issue.id} - no structured JSON and no files changed. "
            "Claude output did not produce any work."
        )
        summary = "No structured result and no files changed"
        notes = f"Parse error: {err}"
    return ImplementationResult(
        issue_id=issue.id,
        success=False,
        summary=summary,
        files_changed=[],
        tests_passed=False,
        notes=notes,
    )


def _run_issue_tests(
    impl,
    issue: Issue,
    impl_result: ImplementationResult,
    session_cont: dict[str, str | None] | None,
    model_pin: str | None,
) -> None:
    if not impl.test_command:
        return
    tests_pass = impl._run_tests(files_changed=impl_result.files_changed)
    impl_result.tests_passed = tests_pass
    if tests_pass:
        return

    impl.logger.warning(f"Tests failed after implementing {issue.id}")
    fix_outcome = impl._fix_failing_tests(
        issue,
        files_changed=impl_result.files_changed,
        session_continuation=session_cont,
        model_override=model_pin,
    )
    if fix_outcome.tests_now_passing:
        impl_result.tests_passed = True
        impl_result.success = True
    elif fix_outcome.accepted_pre_existing_debt and fix_outcome.follow_up_documentation:
        impl_result.tests_passed = False
        impl_result.success = True
        debt = fix_outcome.follow_up_documentation.strip()
        extra = f"\n\nPre-existing / unrelated suite debt (track in follow-up issues):\n{debt}"
        impl_result.notes = ((impl_result.notes or "") + extra).strip()
        impl.logger.info(
            f"{issue.id}: implementation accepted; full test command still fails - "
            "documented pre-existing/unrelated failures in notes for follow-up."
        )
        impl.state.project_wide_tests_unstable = True
    else:
        impl_result.success = False


def _verify_changed_files_if_needed(impl, issue: Issue, impl_result: ImplementationResult) -> None:
    if not impl_result.success:
        return
    actual_changes, detection_ok = impl._get_changed_files(with_status=True)
    if not impl_result.files_changed and actual_changes:
        impl_result.files_changed = actual_changes
    if not detection_ok and not impl.config.get("dry_run"):
        if impl.config.get("strict_change_detection"):
            impl.logger.error(f"{issue.id}: strict change detection enabled and git failed.")
            impl_result.success = False
            impl_result.notes = "Strict change detection failed (git unavailable/timed out)"
        else:
            impl.logger.warning(
                f"{issue.id}: unable to verify file changes (git unavailable/timed out)."
            )
    elif not actual_changes and not impl.config.get("dry_run"):
        if impl.config.get("strict_change_detection"):
            impl.logger.error(f"{issue.id}: strict change detection enabled and no files changed.")
            impl_result.success = False
            impl_result.notes = "Strict change detection failed (no files changed)"
        else:
            impl.logger.warning(
                f"{issue.id}: marked success but no files changed in working tree. "
                "Verify implementation is correct."
            )


def _finalize_issue_result(impl, issue: Issue, impl_result: ImplementationResult) -> bool:
    if impl_result.success:
        issue.status = IssueStatus.IMPLEMENTED
        issue.files_changed = impl_result.files_changed
        issue.implementation_notes += f"\nAttempt {issue.attempt_count}: {impl_result.summary}"
        impl.state.issues_implemented += 1
        impl.logger.info(
            f"Successfully implemented {issue.id} ({len(issue.files_changed)} files changed)"
        )
    else:
        sampled_notes = sample_error_payload(impl_result.notes)
        issue.status = IssueStatus.FAILED
        issue.implementation_notes += (
            f"\nAttempt {issue.attempt_count} failed (sample):\n{sampled_notes}"
        )
        impl.state.issues_failed += 1
        impl.logger.warning(f"Failed to implement {issue.id}: {compact_error_text(sampled_notes)}")

    impl.state.current_issue_id = None
    impl.state.update_issue(issue)
    impl._sync_issue_markdown(issue)
    return impl_result.success
