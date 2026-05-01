"""Implementation engine for AIDLC issue execution and verification."""

import json
import time
import uuid
from pathlib import Path

from . import implementer_finalize
from ._proc import run_with_group_kill
from .implementer_helpers import (
    FixTestsOutcome,
    build_implementation_prompt,
    detect_test_command,
    ensure_test_deps,
    fix_failing_tests,
    implementation_instructions,
    log_provider_result_for_issue,
    reopen_stale_verified_issues,
    reopen_transient_failures,
    reset_outage_failed_attempts,
)
from .implementer_issue_order import sort_issues_for_implementation
from .implementer_signals import (
    SERVICE_OUTAGE_STOP_REASON,
    compact_error_text,
    is_all_models_token_exhausted,
    is_no_models_available,
    is_service_outage,
    is_service_outage_stop_reason,
    sample_error_payload,
    should_stop_for_provider_availability,
)
from .implementer_targeted_tests import effective_implementation_test_command
from .implementer_workspace import (
    get_changed_files,
    git_commit_cycle_snapshot,
    git_current_branch,
    git_has_changes,
    git_push_current_branch,
    prune_aidlc_data,
)
from .logger import log_checkpoint
from .models import Issue, IssueStatus, RunPhase, RunState
from .planner_helpers import render_issue_md
from .reporting import generate_checkpoint_summary
from .routing.helpers import routed_model_from_result
from .schemas import (
    ImplementationResult,
    parse_implementation_result,
)
from .state_manager import checkpoint, save_state
from .timing import add_console_time


class Implementer:
    """Runs the implementation phase of an AIDLC session."""

    def __init__(
        self,
        state: RunState,
        run_dir: Path,
        config: dict,
        cli,
        project_context: str,
        logger,
    ):
        self.state = state
        self.run_dir = run_dir
        self.config = config
        self.cli = cli
        self.project_context = project_context
        self.logger = logger
        self.project_root = Path(config["_project_root"])
        self.test_command = config.get("run_tests_command")
        self.max_attempts = config.get("max_implementation_attempts", 3)
        self.test_timeout = config.get("test_timeout_seconds", 300)
        self.max_impl_context_chars = config.get("max_implementation_context_chars", 9000)
        self.escalate_on_retry = config.get("implementation_escalate_on_retry", True)
        self.complexity_ac_threshold = max(
            1,
            int(config.get("implementation_complexity_acceptance_criteria_threshold", 6)),
        )
        self.complexity_dep_threshold = max(
            1, int(config.get("implementation_complexity_dependencies_threshold", 3))
        )
        self.complexity_description_threshold = max(
            200,
            int(config.get("implementation_complexity_description_chars_threshold", 2500)),
        )
        default_labels = [
            "architecture",
            "security",
            "migration",
            "refactor-core",
            "cross-cutting",
        ]
        raw_complexity_labels = config.get(
            "implementation_complexity_labels",
            default_labels,
        )
        self.complexity_labels = {
            str(label).strip().lower() for label in raw_complexity_labels if str(label).strip()
        }
        self.issues_dir = Path(config["_issues_dir"])

        self.autosync_enabled = bool(config.get("autosync_enabled", True))
        self.autosync_every_cycles = max(
            1, int(config.get("autosync_every_implementation_cycles", 25) or 25)
        )
        self.autosync_finalize_before_push = bool(config.get("autosync_finalize_before_push", True))
        self.autosync_push_remote = bool(config.get("autosync_push_remote", True))
        self.autosync_issue_status_sync = bool(config.get("autosync_issue_status_sync", True))
        self.autosync_commit_message_template = str(
            config.get(
                "autosync_commit_message_template",
                "aidlc: autosync after implementation cycle {cycle}",
            )
        )
        self.autosync_prune_enabled = bool(config.get("autosync_prune_enabled", True))
        self.autosync_runs_to_keep = max(1, int(config.get("autosync_runs_to_keep", 5) or 5))
        self.autosync_keep_claude_outputs = max(
            1, int(config.get("autosync_keep_claude_outputs", 200) or 200)
        )
        # Periodic cleanup cadence: run a subset of finalization passes every
        # N implementation cycles to keep code health high mid-run. Independent
        # of autosync (which is about commit/push). 0 disables the hook.
        self.cleanup_passes_every_cycles = max(
            0, int(config.get("cleanup_passes_every_cycles", 10) or 0)
        )
        raw_periodic_passes = config.get("cleanup_passes_periodic", ["abend", "cleanup"])
        self.cleanup_passes_periodic = [
            str(p).strip().lower() for p in raw_periodic_passes if str(p).strip()
        ]
        self.stop_on_all_models_token_exhausted = bool(
            config.get("stop_on_all_models_token_exhausted", True)
        )

    def _impl_continuation_path(self, issue: Issue) -> Path:
        return (
            self.run_dir
            / "claude_sessions"
            / f"impl_{issue.id}_a{issue.attempt_count:02d}.continuation.json"
        )

    def _legacy_impl_uuid_path(self, issue: Issue) -> Path:
        return self.run_dir / "claude_sessions" / f"impl_{issue.id}_a{issue.attempt_count:02d}.uuid"

    def _save_impl_continuation(self, issue: Issue, data: dict[str, str | None]) -> None:
        path = self._impl_continuation_path(issue)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def _load_or_create_impl_continuation(
        self, issue: Issue, resuming: bool
    ) -> dict[str, str | None]:
        """Per-provider session hints for this issue attempt (Claude / Codex / Copilot)."""
        if not self.config.get("claude_implementation_cli_threading", True):
            return {}
        sess_dir = self.run_dir / "claude_sessions"
        sess_dir.mkdir(parents=True, exist_ok=True)
        path = self._impl_continuation_path(issue)
        legacy = self._legacy_impl_uuid_path(issue)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            if isinstance(raw, dict):
                return {
                    "claude": raw.get("claude") if raw.get("claude") else None,
                    "openai": raw.get("openai") if raw.get("openai") else None,
                    "copilot": raw.get("copilot") if raw.get("copilot") else None,
                }
        if legacy.exists():
            cid = legacy.read_text(encoding="utf-8").strip() or None
            out: dict[str, str | None] = {
                "claude": cid,
                "openai": None,
                "copilot": str(uuid.uuid4()),
            }
            self._save_impl_continuation(issue, out)
            return out
        out = {
            "claude": str(uuid.uuid4()),
            "openai": None,
            "copilot": str(uuid.uuid4()),
        }
        self._save_impl_continuation(issue, out)
        return out

    def _maybe_reopen_transient_failures(self, force_all: bool = False) -> int:
        """Delegate to ``implementer_helpers.reopen_transient_failures`` (kept as a method for callers)."""
        return reopen_transient_failures(
            self.state, self.logger, self._sync_issue_markdown, force_all=force_all
        )

    def _maybe_reopen_stale_verified_issues(self) -> bool:
        """Delegate to ``implementer_helpers.reopen_stale_verified_issues`` (kept as a method for callers)."""
        return reopen_stale_verified_issues(
            self.state,
            self.logger,
            self._sync_issue_markdown,
            enabled=self.config.get("implementation_reopen_verified_without_result", True),
        )

    def _emit_run_checkpoint_summary(self) -> None:
        """Snapshot state to checkpoints/, write markdown summary, log CHECKPOINT block."""
        checkpoint(self.state, self.run_dir)
        reports_dir = Path(self.config["_reports_dir"]) / self.state.run_id
        reports_dir.mkdir(parents=True, exist_ok=True)
        generate_checkpoint_summary(self.state, reports_dir)
        log_checkpoint(self.logger, self.state.to_dict())

    def run(self) -> bool:
        """Run implementation loop until all issues are resolved.

        Returns:
            bool: True when final verification passes, False otherwise.
        """
        checkpoint_interval = self.config.get("checkpoint_interval_minutes", 15) * 60
        last_checkpoint_time = time.time()
        max_consecutive_failures = self.config.get("max_consecutive_failures", 3)
        consecutive_failures = 0

        # Detect test command if not configured
        if not self.test_command:
            self.test_command = self._detect_test_command()
            if self.test_command:
                self.logger.info(f"Auto-detected test command: {self.test_command}")

        # Ensure test dependencies are installed
        if self.test_command and not self.config.get("dry_run"):
            self._ensure_test_deps()

        if self.state.phase == RunPhase.VERIFYING:
            self.logger.info("Resuming final verification (skipping implementation loop)")
            verification_ok = self._verification_pass()
            save_state(self.state, self.run_dir)
            return verification_ok

        # Sort issues by priority and dependency order
        if not self._sort_issues():
            self.state.phase = RunPhase.IMPLEMENTING
            self.state.stop_reason = (
                "Dependency cycle detected. Resolve issue dependencies to continue."
            )
            self.logger.error(self.state.stop_reason)
            save_state(self.state, self.run_dir)
            return False

        # Apply --reset-failed-attempts before any auto-reopen so the helper
        # below sees the corrected attempt_count / cleared status.
        if bool(self.config.get("_reset_failed_attempts_flag", False)):
            n_reset = reset_outage_failed_attempts(
                self.state, self.logger, self._sync_issue_markdown
            )
            if n_reset:
                self.logger.info(
                    f"--reset-failed-attempts: reset {n_reset} outage-marked issue(s)."
                )
            else:
                self.logger.info("--reset-failed-attempts: no outage-marked failed issues found.")

        # Auto-reopen transient failures (or all if --retry-failed).
        force_retry = bool(self.config.get("_retry_failed_flag", False))
        reopened = self._maybe_reopen_transient_failures(force_all=force_retry)
        if reopened:
            self.logger.info(
                f"Reopened {reopened} previously-failed issue(s) for retry "
                f"({'forced via --retry-failed' if force_retry else 'transient causes'})."
            )

        if self._maybe_reopen_stale_verified_issues():
            if not self._sort_issues():
                self.state.phase = RunPhase.IMPLEMENTING
                self.state.stop_reason = (
                    "Dependency cycle detected after re-opening verified issues."
                )
                self.logger.error(self.state.stop_reason)
                save_state(self.state, self.run_dir)
                return False

        self.state.phase = RunPhase.IMPLEMENTING
        save_state(self.state, self.run_dir)
        self.logger.info("Starting implementation phase")
        self.logger.info(f"  Total issues: {self.state.total_issues}")
        self.logger.info(f"  Test command: {self.test_command or 'none'}")

        # Dry-run cycle cap
        max_cycles = self.config.get("max_implementation_cycles", 0)
        if self.config.get("dry_run") and max_cycles == 0:
            max_cycles = 3

        while not self.state.all_issues_resolved():
            # Cycle cap for dry-run
            if max_cycles and self.state.implementation_cycles >= max_cycles:
                self.state.stop_reason = f"Max implementation cycles ({max_cycles})"
                self.logger.info("Max implementation cycles reached.")
                break

            # Get next issue to work on
            pending = self.state.get_pending_issues()
            if not pending:
                # Check if we're truly stuck (all remaining are blocked/exhausted)
                blocked_count = sum(
                    1
                    for d in self.state.issues
                    if d.get("status") in ("pending", "blocked", "failed")
                )
                if blocked_count > 0:
                    self.state.stop_reason = (
                        f"{blocked_count} issues blocked by unmet dependencies. "
                        "Resolve dependencies to continue."
                    )
                    self.logger.error(self.state.stop_reason)
                    break
                else:
                    break

            issue = pending[0]
            # The "===" header is logged inside `_implement_issue` so it can
            # honestly distinguish "starting attempt N+1" from "resuming
            # interrupted attempt N" — see the IN_PROGRESS-on-entry branch
            # at the top of `_implement_issue`.

            # Implement
            success = self._implement_issue(issue)

            self.state.implementation_cycles += 1

            outage_signal = is_service_outage_stop_reason(self.state.stop_reason)

            if success:
                consecutive_failures = 0
            elif outage_signal:
                # Outage isn't signal about issue ordering — don't bump
                # consecutive_failures or trigger the re-sort path.
                pass
            else:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    self.logger.warning(
                        f"{max_consecutive_failures} consecutive failures. "
                        "Pausing to re-sort and try different issues."
                    )
                    consecutive_failures = 0
                    if not self._sort_issues():
                        self.state.stop_reason = (
                            "Dependency cycle detected while re-sorting. "
                            "Resolve dependencies to continue."
                        )
                        self.logger.error(self.state.stop_reason)
                        break

            if outage_signal:
                pause = max(0, int(self.config.get("implementation_outage_pause_seconds", 300)))
                self.logger.warning(
                    f"Service-outage signal received; pausing {pause}s before next iteration."
                )
                save_state(self.state, self.run_dir)
                if pause > 0:
                    time.sleep(pause)
                # Clear the outage stop_reason so the early-stop logger at the
                # bottom of run() does not treat this as a terminal stop.
                self.state.stop_reason = None
                continue

            if should_stop_for_provider_availability(self.state.stop_reason):
                self.logger.error(
                    "Stopping implementation loop due to provider/model availability."
                )
                break

            save_state(self.state, self.run_dir)

            if self._should_autosync():
                if self._autosync_progress():
                    self._emit_run_checkpoint_summary()
                    last_checkpoint_time = time.time()

            if self._should_run_periodic_cleanup():
                self._run_periodic_cleanup()

            if time.time() - last_checkpoint_time >= checkpoint_interval:
                self._emit_run_checkpoint_summary()
                last_checkpoint_time = time.time()

        if self.state.stop_reason and not self.state.all_issues_resolved():
            # Don't auto-run finalization on early stop unless the user
            # opts in. The default-off prevents burning more budget at
            # exactly the moment we want to stop cleanly (e.g. token
            # exhaustion). Always log a single visually-distinct stop-reason
            # line and resume hint so the user can see what happened without
            # parsing the rest of the log.
            remaining = sum(
                1
                for d in self.state.issues
                if d.get("status") in ("pending", "in_progress", "blocked", "failed")
            )
            self.logger.error("=" * 60)
            self.logger.error(f"STOP REASON: {self.state.stop_reason}")
            self.logger.error(f"Issues remaining: {remaining}")
            self.logger.error("RESUME WITH: aidlc run --resume")
            self.logger.error("=" * 60)

            if self.config.get("implementation_finalize_on_early_stop", False):
                self.logger.info(
                    "implementation_finalize_on_early_stop=true: running finalization "
                    "passes (cleanup) before exit."
                )
                try:
                    from .finalizer import Finalizer

                    finalizer = Finalizer(
                        self.state,
                        self.run_dir,
                        self.config,
                        self.cli,
                        self.project_context,
                        self.logger,
                    )
                    finalizer.run(passes=["cleanup"])
                except Exception as e:
                    self.logger.error(f"Finalization passes failed: {e}")
            else:
                self.logger.info(
                    "Skipping early-stop finalization passes "
                    "(set implementation_finalize_on_early_stop=true to opt back in)."
                )
            save_state(self.state, self.run_dir)
            return False

        # Final verification pass
        self.logger.info("Running final verification pass...")
        verification_ok = self._verification_pass()

        save_state(self.state, self.run_dir)
        return verification_ok

    def _implement_issue(self, issue: Issue) -> bool:
        """Implement a single issue. Returns True on success."""
        # Detect resume-of-interrupted-attempt. State is persisted with
        # status=IN_PROGRESS and attempt_count already incremented at the
        # *start* of an attempt (see further down in this method); if we
        # see IN_PROGRESS on entry, the previous attempt was killed mid-
        # flight (Ctrl-C, SIGTERM, OOM, hard timeout). Restart the same
        # attempt rather than burning a fresh attempt_count slot — one
        # killed attempt should not consume two of max_attempts.
        resuming_interrupted = issue.status == IssueStatus.IN_PROGRESS
        if resuming_interrupted:
            self.logger.info(
                f"=== Resuming interrupted {issue.id}: {issue.title} "
                f"(attempt {issue.attempt_count}/{issue.max_attempts}) ==="
            )
            self.logger.warning(
                f"{issue.id}: previous attempt was killed mid-flight. "
                "Working tree may contain partial changes from that attempt; "
                "the model will see them and decide whether to extend or revert."
            )
        else:
            issue.attempt_count += 1
            self.logger.info(
                f"=== Implementing {issue.id}: {issue.title} "
                f"(attempt {issue.attempt_count}/{issue.max_attempts}) ==="
            )
        issue.status = IssueStatus.IN_PROGRESS
        self.state.current_issue_id = issue.id
        self.state.update_issue(issue)
        self._sync_issue_markdown(issue)

        use_impl_threading = self.config.get("claude_implementation_cli_threading", True)
        self._impl_continuation = (
            self._load_or_create_impl_continuation(issue, resuming_interrupted)
            if use_impl_threading
            else {}
        )
        session_cont = self._impl_continuation if use_impl_threading else None
        impl_model_pin: str | None = None

        # Build prompt
        prompt = self._build_implementation_prompt(issue)
        self.logger.info(f"  Prompt size: {len(prompt):,} chars (~{len(prompt) // 4:,} tokens)")
        is_complex = self._is_complex_issue(issue)
        # Signal complexity to router so it can apply phase-aware model selection
        if hasattr(self.cli, "set_complexity"):
            complexity = "complex" if is_complex else "normal"
            self.cli.set_complexity(complexity)

        # Execute with file edit permissions; router selects provider/model.
        start_time = time.time()
        result = self.cli.execute_prompt(
            prompt,
            self.project_root,
            allow_edits=True,
            model_override=impl_model_pin,
            session_continuation=session_cont,
        )
        if (
            use_impl_threading
            and result.get("success")
            and result.get("provider_id") == "claude"
        ):
            delay = float(self.config.get("claude_session_release_delay_seconds", 2.0))
            if delay > 0:
                time.sleep(delay)
        self._log_provider_result(issue, result)
        self.state.record_provider_result(result, self.config, phase="implementation")
        if use_impl_threading and result.get("success"):
            m = routed_model_from_result(result)
            if m:
                impl_model_pin = m
            if result.get("provider_id") == "openai":
                ext = result.get("continuation_session_id")
                if ext:
                    self._impl_continuation["openai"] = ext
                    self._save_impl_continuation(issue, self._impl_continuation)
            elif result.get("provider_id") == "claude":
                ext = result.get("continuation_session_id")
                if ext:
                    self._impl_continuation["claude"] = ext
                    self._save_impl_continuation(issue, self._impl_continuation)
        duration = time.time() - start_time
        self.state.elapsed_seconds += duration

        # Save raw output
        output_text = result.get("output", "")
        if output_text:
            output_dir = self.run_dir / "claude_outputs"
            output_dir.mkdir(exist_ok=True)
            (output_dir / f"impl_{issue.id}_{issue.attempt_count:02d}.md").write_text(output_text)

        if not result["success"]:
            sampled_error = sample_error_payload(result.get("error"))
            compact_error = compact_error_text(sampled_error)
            self.logger.error(f"Implementation of {issue.id} failed: {compact_error}")
            # Tag the failure cause so the next implementation cycle can
            # decide whether to auto-reopen (transient causes) or leave for
            # manual review (real blockers like dependency / test regression).
            from .issue_model import (
                FAILURE_CAUSE_TOKEN_EXHAUSTED,
                FAILURE_CAUSE_UNKNOWN,
            )

            if is_service_outage(result):
                # Outage failures aren't signal about the issue. Roll back the
                # attempt increment from earlier in this method and leave
                # status=PENDING so the run loop's outage-pause path (and a
                # later --resume) can pick the issue up again. The marker in
                # implementation_notes lets `aidlc run --reset-failed-attempts`
                # find these issues if a future code path ever does mark them
                # failed during an outage.
                if not resuming_interrupted and issue.attempt_count > 0:
                    issue.attempt_count -= 1
                issue.status = IssueStatus.PENDING
                issue.failure_cause = None
                issue.implementation_notes += (
                    "\n[outage] attempt rolled back due to Claude service outage"
                )
                self.state.stop_reason = SERVICE_OUTAGE_STOP_REASON
                self.state.update_issue(issue)
                self._sync_issue_markdown(issue)
                return False
            if is_all_models_token_exhausted(result):
                message = (
                    "All available models/providers appear out of tokens or quota; "
                    "stopping run to allow safe resume later."
                )
                self.state.stop_reason = message
                self.logger.error(message)
                issue.failure_cause = FAILURE_CAUSE_TOKEN_EXHAUSTED
            elif is_no_models_available(result):
                message = (
                    "No models/providers are currently available; "
                    "saving state and exiting for clean resume."
                )
                self.state.stop_reason = message
                self.logger.error(message)
                issue.failure_cause = FAILURE_CAUSE_TOKEN_EXHAUSTED
            else:
                issue.failure_cause = FAILURE_CAUSE_UNKNOWN
            issue.status = IssueStatus.FAILED
            issue.implementation_notes += (
                f"\nAttempt {issue.attempt_count} failed (sample):\n{sampled_error}"
            )
            self.state.update_issue(issue)
            self._sync_issue_markdown(issue)
            return False

        # Parse implementation result
        if self.config.get("dry_run"):
            impl_result = ImplementationResult(
                issue_id=issue.id,
                success=True,
                summary="[DRY RUN]",
                files_changed=[],
                tests_passed=True,
            )
        else:
            try:
                impl_result = parse_implementation_result(output_text)
            except ValueError as e:
                # Claude did not return parseable JSON. The structured-JSON
                # contract is for communication (telling us what changed,
                # why); git diff is the source of truth for verification.
                # When the model wrote files but the JSON envelope is
                # missing/garbled (e.g. mid-output timeout, trailing
                # prose), trust the diff and proceed — the next test step
                # is the real gate. Throwing the work away and retrying
                # the entire issue is wildly expensive (~$5/attempt in
                # cache reads) and almost never produces a different
                # outcome.
                self.logger.warning(f"No structured JSON result for {issue.id}: {e}")
                changed_files, detection_ok = self._get_changed_files(with_status=True)
                if changed_files and detection_ok:
                    self.logger.warning(
                        f"{issue.id}: accepting {len(changed_files)} file change(s) from git "
                        "diff despite missing/garbled JSON envelope; tests will gate."
                    )
                    impl_result = ImplementationResult(
                        issue_id=issue.id,
                        success=True,
                        summary=(
                            f"Synthesized from git diff: {len(changed_files)} file(s) "
                            "changed (no JSON envelope from model)"
                        ),
                        files_changed=changed_files,
                        # tests_passed is set by the test step below; default
                        # False so a missing test step doesn't silently mark verified.
                        tests_passed=False,
                        notes=f"JSON parse failed ({e}); accepted via git-diff verification",
                    )
                elif changed_files and not detection_ok:
                    self.logger.error(
                        f"FAIL: {issue.id} — file change detection unavailable; cannot safely "
                        "accept unstructured output."
                    )
                    impl_result = ImplementationResult(
                        issue_id=issue.id,
                        success=False,
                        summary="Unstructured result with unavailable change detection",
                        files_changed=[],
                        tests_passed=False,
                        notes="Change detection unavailable",
                    )
                else:
                    self.logger.error(
                        f"FAIL: {issue.id} — no structured JSON and no files changed. "
                        f"Claude output did not produce any work."
                    )
                    impl_result = ImplementationResult(
                        issue_id=issue.id,
                        success=False,
                        summary="No structured result and no files changed",
                        files_changed=[],
                        tests_passed=False,
                        notes=f"Parse error: {e}",
                    )

        # Run tests if available
        if self.test_command:
            tests_pass = self._run_tests(files_changed=impl_result.files_changed)
            impl_result.tests_passed = tests_pass
            if not tests_pass:
                self.logger.warning(f"Tests failed after implementing {issue.id}")
                fix_outcome = self._fix_failing_tests(
                    issue,
                    files_changed=impl_result.files_changed,
                    session_continuation=session_cont,
                    model_override=impl_model_pin,
                )
                if fix_outcome.tests_now_passing:
                    impl_result.tests_passed = True
                    # Model JSON often has success=false while tests were red; we verified green.
                    impl_result.success = True
                elif fix_outcome.accepted_pre_existing_debt and fix_outcome.follow_up_documentation:
                    impl_result.tests_passed = False
                    # Same: JSON may say success=false because the full command failed; we explicitly accept.
                    impl_result.success = True
                    debt = fix_outcome.follow_up_documentation.strip()
                    extra = (
                        "\n\nPre-existing / unrelated suite debt "
                        "(track in follow-up issues):\n"
                        f"{debt}"
                    )
                    impl_result.notes = ((impl_result.notes or "") + extra).strip()
                    self.logger.info(
                        f"{issue.id}: implementation accepted; full test command still fails — "
                        "documented pre-existing/unrelated failures in notes for follow-up."
                    )
                    self.state.project_wide_tests_unstable = True
                else:
                    impl_result.success = False

        if impl_result.success:
            # Validate that files were actually changed
            actual_changes, detection_ok = self._get_changed_files(with_status=True)
            if not impl_result.files_changed and actual_changes:
                impl_result.files_changed = actual_changes
            if not detection_ok and not self.config.get("dry_run"):
                if self.config.get("strict_change_detection"):
                    self.logger.error(
                        f"{issue.id}: strict change detection enabled and git verification failed."
                    )
                    impl_result.success = False
                    impl_result.notes = "Strict change detection failed (git unavailable/timed out)"
                else:
                    self.logger.warning(
                        f"{issue.id}: unable to verify file changes (git unavailable/timed out)."
                    )
            elif not actual_changes and not self.config.get("dry_run"):
                if self.config.get("strict_change_detection"):
                    self.logger.error(
                        f"{issue.id}: strict change detection enabled and no files changed."
                    )
                    impl_result.success = False
                    impl_result.notes = "Strict change detection failed (no files changed)"
                else:
                    self.logger.warning(
                        f"{issue.id}: marked success but no files changed in working tree. "
                        f"Verify implementation is correct."
                    )

        if impl_result.success:
            issue.status = IssueStatus.IMPLEMENTED
            issue.files_changed = impl_result.files_changed
            issue.implementation_notes += f"\nAttempt {issue.attempt_count}: {impl_result.summary}"
            self.state.issues_implemented += 1
            self.logger.info(
                f"Successfully implemented {issue.id} ({len(issue.files_changed)} files changed)"
            )
        else:
            sampled_notes = sample_error_payload(impl_result.notes)
            issue.status = IssueStatus.FAILED
            issue.implementation_notes += (
                f"\nAttempt {issue.attempt_count} failed (sample):\n{sampled_notes}"
            )
            self.state.issues_failed += 1
            self.logger.warning(
                f"Failed to implement {issue.id}: {compact_error_text(sampled_notes)}"
            )

        self.state.current_issue_id = None
        self.state.update_issue(issue)
        self._sync_issue_markdown(issue)
        return impl_result.success

    def _build_implementation_prompt(self, issue: Issue) -> str:
        return build_implementation_prompt(self, issue)

    def _implementation_instructions(self) -> str:
        return implementation_instructions(self.test_command)

    def _fix_failing_tests(
        self,
        issue: Issue,
        model_override: str | None = None,
        *,
        files_changed: list[str] | None = None,
        session_continuation: dict[str, str | None] | None = None,
    ) -> FixTestsOutcome:
        """Give Claude a chance to fix failing tests."""
        return fix_failing_tests(
            self,
            issue,
            model_override=model_override,
            files_changed=files_changed,
            session_continuation=session_continuation,
        )

    def _is_complex_issue(self, issue: Issue) -> bool:
        """Return whether an issue should use the complex implementation path."""
        is_complex = False
        reasons = []

        if self.escalate_on_retry and issue.attempt_count >= 2:
            is_complex = True
            reasons.append("retry")

        if len(issue.acceptance_criteria or []) >= self.complexity_ac_threshold:
            is_complex = True
            reasons.append("acceptance_criteria")

        if len(issue.dependencies or []) >= self.complexity_dep_threshold:
            is_complex = True
            reasons.append("dependencies")

        if len((issue.description or "").strip()) >= self.complexity_description_threshold:
            is_complex = True
            reasons.append("description_size")

        if self.complexity_labels:
            labels = {str(label).strip().lower() for label in (issue.labels or [])}
            if labels.intersection(self.complexity_labels):
                is_complex = True
                reasons.append("labels")

        if reasons:
            self.logger.info(
                f"{issue.id}: using implementation_complex routing (complexity: {', '.join(reasons)})"
            )
        else:
            self.logger.info(f"{issue.id}: using standard implementation routing")
        return is_complex

    def _log_provider_result(self, issue: Issue, result: dict) -> None:
        """Delegate to ``implementer_helpers.log_provider_result_for_issue``."""
        log_provider_result_for_issue(self.logger, issue, result)

    def _ensure_test_deps(self):
        ensure_test_deps(self.project_root, self.test_command, self.logger, state=self.state)

    def _run_tests(
        self,
        capture_output: bool = False,
        *,
        files_changed: list[str] | None = None,
        use_targeted_if_unstable: bool = True,
    ) -> bool | str:
        """Run the project's test suite.

        If capture_output is True, returns the output string instead of bool.
        """
        if not self.test_command:
            return True if not capture_output else ""

        if self.config.get("dry_run"):
            return True if not capture_output else "[DRY RUN] Tests passed"

        cmd = self.test_command
        if use_targeted_if_unstable:
            cmd = effective_implementation_test_command(
                self.project_root,
                self.test_command,
                files_changed,
                project_wide_tests_unstable=self.state.project_wide_tests_unstable,
                config=self.config,
            )
            if cmd != self.test_command:
                self.logger.info(
                    f"Running tests (targeted; project-wide suite unstable): {cmd[:240]}"
                    f"{'…' if len(cmd) > 240 else ''}"
                )

        t0 = time.time()
        try:
            try:
                result = run_with_group_kill(
                    cmd,
                    cwd=str(self.project_root),
                    timeout=self.test_timeout,
                )
            except Exception as e:
                self.logger.error(f"Failed to run tests: {e}")
                if capture_output:
                    return f"Failed to run tests: {e}"
                return False
            if result.timed_out:
                self.logger.warning(f"Test suite timed out ({self.test_timeout}s)")
                if capture_output:
                    return f"Tests timed out after {self.test_timeout}s"
                return False
            if capture_output:
                return result.stdout + result.stderr
            return result.returncode == 0
        finally:
            add_console_time(self.state, t0)

    def _verification_pass(self) -> bool:
        """Final pass to verify all implemented issues.

        Returns:
            bool: True when final verification is successful.
        """
        self.state.phase = RunPhase.VERIFYING

        for d in self.state.issues:
            if d.get("status") == "implemented":
                issue = Issue.from_dict(d)
                # Mark as verified (tests already passed during implementation)
                issue.status = IssueStatus.VERIFIED
                self.state.update_issue(issue)
                self._sync_issue_markdown(issue)
                self.state.issues_verified += 1

        # Run full test suite one last time
        if self.test_command:
            self.logger.info("Running final test suite...")
            tests_pass = self._run_tests(use_targeted_if_unstable=False)
            if tests_pass:
                self.logger.info("All tests pass.")
            else:
                self.logger.error("Final test suite has failures.")
                self.state.validation_results.append("Final test suite has failures")
                if self.config.get("fail_on_final_test_failure"):
                    self.state.stop_reason = "Final verification failed: test suite has failures"
                    return False
        return True

    def _sort_issues(self) -> bool:
        """Sort issues by priority and dependency order (topological)."""
        return sort_issues_for_implementation(
            self.state, self.logger, self._sync_all_issue_markdown
        )

    def _get_changed_files(self, with_status: bool = False) -> list[str] | tuple[list[str], bool]:
        """Get list of files changed in the working tree (unstaged + staged) via git."""
        return get_changed_files(self.project_root, self.state, self.logger, with_status)

    def _detect_test_command(self) -> str | None:
        return detect_test_command(self.project_root)

    def _should_autosync(self) -> bool:
        if not self.autosync_enabled or self.config.get("dry_run"):
            return False
        return self.state.implementation_cycles > 0 and (
            self.state.implementation_cycles % self.autosync_every_cycles == 0
        )

    def _should_run_periodic_cleanup(self) -> bool:
        return implementer_finalize.should_run_periodic_cleanup(self)

    def _run_periodic_cleanup(self) -> None:
        implementer_finalize.run_periodic_cleanup(self)

    def _autosync_finalize_before_push_if_enabled(self) -> None:
        implementer_finalize.run_finalize_before_push_if_enabled(self)

    def _autosync_progress(self) -> bool:
        """Persist issue statuses, commit, push, and prune stale run artifacts.

        Returns True when a new git commit was created (caller may emit checkpoint summary).
        """
        self.logger.info(
            f"Autosync checkpoint at implementation cycle {self.state.implementation_cycles}"
        )

        self._autosync_finalize_before_push_if_enabled()

        if self.autosync_issue_status_sync:
            self._sync_all_issue_markdown()

        committed = self._git_commit_cycle_snapshot(self.state.implementation_cycles)
        if committed and self.autosync_push_remote:
            self._git_push_current_branch()

        if self.autosync_prune_enabled:
            self._prune_aidlc_data()

        return committed

    def _sync_issue_markdown(self, issue: Issue) -> None:
        """Keep .aidlc issue markdown in sync with in-memory state status/notes."""
        if not self.autosync_issue_status_sync:
            return
        try:
            self.issues_dir.mkdir(parents=True, exist_ok=True)
            issue_path = self.issues_dir / f"{issue.id}.md"
            issue_path.write_text(render_issue_md(issue))
        except OSError as e:
            self.logger.warning(f"Failed to sync issue file for {issue.id}: {e}")

    def _sync_all_issue_markdown(self) -> None:
        for d in self.state.issues:
            try:
                self._sync_issue_markdown(Issue.from_dict(d))
            except Exception as e:
                issue_id = d.get("id", "unknown") if isinstance(d, dict) else "unknown"
                self.logger.warning(f"Issue sync skipped for {issue_id}: {e}")

    def _git_commit_cycle_snapshot(self, cycle_num: int) -> bool:
        """Create an autosync commit when there are uncommitted changes."""
        return git_commit_cycle_snapshot(
            self.project_root,
            cycle_num,
            self.logger,
            self.state,
            self.autosync_commit_message_template,
        )

    def _git_push_current_branch(self) -> bool:
        """Push the current branch to its configured upstream remote."""
        return git_push_current_branch(self.project_root, self.logger, self.state)

    def _git_current_branch(self) -> str | None:
        return git_current_branch(self.project_root, self.state, self.logger)

    def _git_has_changes(self) -> bool:
        return git_has_changes(self.project_root, self.state, self.logger)

    def _prune_aidlc_data(self) -> None:
        """Prune stale .aidlc run artifacts while keeping current and most recent history."""
        prune_aidlc_data(
            self.project_root,
            self.run_dir,
            self.state,
            self.logger,
            self.autosync_runs_to_keep,
            self.autosync_keep_claude_outputs,
        )
