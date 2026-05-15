"""Implementation engine for AIDLC issue execution and verification."""

import time
from pathlib import Path

from . import (
    implementer_autosync,
    implementer_finalize,
    implementer_issue_execution,
    implementer_sessions,
    implementer_settings,
)
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
    is_service_outage_stop_reason,
    should_stop_for_provider_availability,
)
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
from .reporting import generate_checkpoint_summary
from .schemas import parse_implementation_result
from .state_manager import checkpoint, save_state

_LEGACY_TEST_PATCH_POINTS = (run_with_group_kill, parse_implementation_result)


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
        implementer_settings.apply_config(self, config)

    def _impl_continuation_path(self, issue: Issue) -> Path:
        return implementer_sessions.impl_continuation_path(self.run_dir, issue)

    def _legacy_impl_uuid_path(self, issue: Issue) -> Path:
        return implementer_sessions.legacy_impl_uuid_path(self.run_dir, issue)

    def _save_impl_continuation(self, issue: Issue, data: dict[str, str | None]) -> None:
        implementer_sessions.save_impl_continuation(self.run_dir, issue, data)

    def _load_or_create_impl_continuation(
        self, issue: Issue, resuming: bool
    ) -> dict[str, str | None]:
        return implementer_sessions.load_or_create_impl_continuation(
            self.run_dir, issue, self.config, resuming
        )

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
        checkpoint_interval = self.config.get("checkpoint_interval_minutes", 45) * 60
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
        return implementer_issue_execution.implement_issue(self, issue)

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
        return implementer_issue_execution.is_complex_issue(self, issue)

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
        return implementer_issue_execution.run_tests(
            self,
            capture_output=capture_output,
            files_changed=files_changed,
            use_targeted_if_unstable=use_targeted_if_unstable,
        )

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
        return implementer_autosync.should_autosync(self)

    def _should_run_periodic_cleanup(self) -> bool:
        return implementer_finalize.should_run_periodic_cleanup(self)

    def _run_periodic_cleanup(self) -> None:
        implementer_finalize.run_periodic_cleanup(self)

    def _autosync_finalize_before_push_if_enabled(self) -> None:
        implementer_finalize.run_finalize_before_push_if_enabled(self)

    def _autosync_progress(self) -> bool:
        return implementer_autosync.autosync_progress(self)

    def _sync_issue_markdown(self, issue: Issue) -> None:
        implementer_autosync.sync_issue_markdown(self, issue)

    def _sync_all_issue_markdown(self) -> None:
        implementer_autosync.sync_all_issue_markdown(self)

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
            self.autosync_keep_provider_outputs,
        )
