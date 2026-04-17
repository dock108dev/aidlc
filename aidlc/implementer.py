"""Implementation engine for AIDLC issue execution and verification."""

import subprocess
import time
from pathlib import Path

from .implementer_helpers import (
    build_implementation_prompt,
    detect_test_command,
    ensure_test_deps,
    fix_failing_tests,
    implementation_instructions,
)
from .implementer_issue_order import sort_issues_for_implementation
from .implementer_signals import (
    compact_error_text,
    is_all_models_token_exhausted,
    is_no_models_available,
    sample_error_payload,
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
from .planner_helpers import render_issue_md
from .reporting import generate_checkpoint_summary
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
        self.max_impl_context_chars = config.get("max_implementation_context_chars", 30000)
        self.escalate_on_retry = config.get("implementation_escalate_on_retry", True)
        self.complexity_ac_threshold = max(
            1, int(config.get("implementation_complexity_acceptance_criteria_threshold", 6))
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
        self.stop_on_all_models_token_exhausted = bool(
            config.get("stop_on_all_models_token_exhausted", True)
        )

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
            self.logger.info(
                f"=== Implementing {issue.id}: {issue.title} "
                f"(attempt {issue.attempt_count + 1}/{issue.max_attempts}) ==="
            )

            # Implement
            success = self._implement_issue(issue)

            self.state.implementation_cycles += 1

            if success:
                consecutive_failures = 0
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

            if should_stop_for_provider_availability(self.state.stop_reason):
                self.logger.error(
                    "Stopping implementation loop due to provider/model availability."
                )
                break

            save_state(self.state, self.run_dir)

            if self._should_autosync():
                self._autosync_progress()

            # Checkpoint
            if time.time() - last_checkpoint_time >= checkpoint_interval:
                checkpoint(self.state, self.run_dir)
                reports_dir = Path(self.config["_reports_dir"]) / self.state.run_id
                reports_dir.mkdir(parents=True, exist_ok=True)
                generate_checkpoint_summary(self.state, reports_dir)
                log_checkpoint(self.logger, self.state.to_dict())
                last_checkpoint_time = time.time()

        if self.state.stop_reason and not self.state.all_issues_resolved():
            self.logger.info(
                "Skipping final verification because implementation stopped early: "
                f"{self.state.stop_reason}"
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
        issue.status = IssueStatus.IN_PROGRESS
        issue.attempt_count += 1
        self.state.current_issue_id = issue.id
        self.state.update_issue(issue)
        self._sync_issue_markdown(issue)

        # Build prompt
        prompt = self._build_implementation_prompt(issue)
        self.logger.debug(f"Implementation prompt: {len(prompt)} chars")
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
        )
        self._log_provider_result(issue, result)
        self.state.record_provider_result(result, self.config, phase="implementation")
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
            if is_all_models_token_exhausted(result):
                message = (
                    "All available models/providers appear out of tokens or quota; "
                    "stopping run to allow safe resume later."
                )
                self.state.stop_reason = message
                self.logger.error(message)
            elif is_no_models_available(result):
                message = (
                    "No models/providers are currently available; "
                    "saving state and exiting for clean resume."
                )
                self.state.stop_reason = message
                self.logger.error(message)
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
                self.logger.warning(f"No structured JSON result for {issue.id}: {e}")
                # Check if Claude actually changed files via git diff
                changed_files, detection_ok = self._get_changed_files(with_status=True)
                if changed_files and detection_ok:
                    self.logger.info(
                        f"No JSON result but {len(changed_files)} files changed — "
                        "rejecting unstructured implementation path"
                    )
                    impl_result = ImplementationResult(
                        issue_id=issue.id,
                        success=False,
                        summary="Unstructured output with file changes is not accepted",
                        files_changed=changed_files,
                        tests_passed=False,
                        notes="Structured JSON output is required",
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
            tests_pass = self._run_tests()
            impl_result.tests_passed = tests_pass
            if not tests_pass:
                self.logger.warning(f"Tests failed after implementing {issue.id}")
                # Give Claude a chance to fix
                fix_success = self._fix_failing_tests(issue)
                if fix_success:
                    impl_result.tests_passed = True
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

    def _fix_failing_tests(self, issue: Issue, model_override: str | None = None) -> bool:
        """Give Claude a chance to fix failing tests."""
        return fix_failing_tests(self, issue, model_override=model_override)

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
        """Log which provider/model handled an implementation call."""
        provider = str(result.get("provider_id") or "unknown")
        model = str(result.get("model_used") or "unknown")
        routing = result.get("routing_decision") or {}
        requested_model = str(routing.get("model") or model)
        if model != requested_model and model != "unknown":
            self.logger.info(
                f"{issue.id}: model {provider}/{model} (requested {provider}/{requested_model})"
            )
        else:
            self.logger.info(f"{issue.id}: model {provider}/{requested_model}")

    def _ensure_test_deps(self):
        ensure_test_deps(self.project_root, self.test_command, self.logger, state=self.state)

    def _run_tests(self, capture_output: bool = False) -> bool | str:
        """Run the project's test suite.

        If capture_output is True, returns the output string instead of bool.
        """
        if not self.test_command:
            return True if not capture_output else ""

        if self.config.get("dry_run"):
            return True if not capture_output else "[DRY RUN] Tests passed"

        t0 = time.time()
        try:
            proc = subprocess.run(
                self.test_command,
                shell=True,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=self.test_timeout,
            )
            if capture_output:
                return proc.stdout + proc.stderr
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Test suite timed out ({self.test_timeout}s)")
            if capture_output:
                return f"Tests timed out after {self.test_timeout}s"
            return False
        except Exception as e:
            self.logger.error(f"Failed to run tests: {e}")
            if capture_output:
                return f"Failed to run tests: {e}"
            return False
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
            tests_pass = self._run_tests()
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

    def _autosync_progress(self) -> None:
        """Persist issue statuses, commit, push, and prune stale run artifacts."""
        self.logger.info(
            f"Autosync checkpoint at implementation cycle {self.state.implementation_cycles}"
        )

        if self.autosync_issue_status_sync:
            self._sync_all_issue_markdown()

        committed = self._git_commit_cycle_snapshot(self.state.implementation_cycles)
        if committed and self.autosync_push_remote:
            self._git_push_current_branch()

        if self.autosync_prune_enabled:
            self._prune_aidlc_data()

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
