"""Validation loop engine for AIDLC.

After implementation, runs stack-specific test suites, parses failures,
generates fix issues, and loops until stable or max iterations reached.
"""

import subprocess
import time
from pathlib import Path

from .models import RunState, RunPhase, Issue, IssueStatus
from .state_manager import save_state
from .test_parser import parse_test_failures, FailureReport
from .test_profiles import detect_test_profile
from .validation_issues import create_fix_issues
from .context_utils import parse_project_type
from .timing import add_console_time


class Validator:
    """Runs the validation loop: test → parse failures → fix → re-test."""

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

        # Detect test profile
        project_type = parse_project_type(project_context)

        self.test_profile = detect_test_profile(self.project_root, project_type, config)
        self.max_cycles = config.get("validation_max_cycles", 3)
        self.batch_size = config.get("validation_batch_size", 10)
        self.test_timeout = config.get("test_timeout_seconds", 300)
        self.test_profile_mode = config.get("test_profile_mode", "progressive")
        if self.test_profile_mode != "progressive":
            raise RuntimeError("Legacy path removed — use SSOT implementation")

    def run(self) -> bool:
        """Run the validation loop. Returns True if project is stable."""
        self.state.phase = RunPhase.VALIDATING
        save_state(self.state, self.run_dir)

        self.logger.info("Starting validation loop...")
        self.logger.info(f"  Test profile: {self.test_profile}")
        self.logger.info(f"  Max cycles: {self.max_cycles}")

        # Check if we have any test commands at all
        has_tests = any(v for v in self.test_profile.values() if v)
        if not has_tests:
            if self.config.get("strict_validation") and not self.config.get("validation_allow_no_tests", True):
                self.logger.error(
                    "No test commands detected and strict validation forbids skipping."
                )
                return False
            self.logger.info("No test commands detected — skipping validation")
            return True

        previous_failure_count = None

        for cycle in range(self.max_cycles):
            self.state.validation_cycles += 1
            self.logger.info(f"=== Validation Cycle {cycle + 1}/{self.max_cycles} ===")

            # Run test tiers
            all_passed, failures, tier_results = self._run_test_tiers()

            # Record results
            self.state.validation_test_results.append({
                "cycle": cycle + 1,
                "passed": all_passed,
                "failure_count": len(failures),
                "tier_results": tier_results,
            })
            save_state(self.state, self.run_dir)

            if all_passed:
                self.logger.info("All tests passed — project is stable!")
                return True

            self.logger.info(f"Found {len(failures)} test failure(s)")

            # Check if we're making progress
            if previous_failure_count is not None:
                if len(failures) >= previous_failure_count:
                    self.logger.warning(
                        f"Not making progress: {len(failures)} failures "
                        f"(was {previous_failure_count}). Stopping validation."
                    )
                    break
            previous_failure_count = len(failures)

            # Generate fix issues
            existing_ids = {d["id"] for d in self.state.issues}
            fix_counter = self.state.validation_issues_created + 1
            new_issues = create_fix_issues(
                failures, existing_ids,
                max_issues=self.batch_size,
                base_id_counter=fix_counter,
            )

            if not new_issues:
                self.logger.info("No actionable fix issues generated — stopping")
                break

            # Add fix issues to state
            for issue in new_issues:
                self.state.update_issue(issue)
                self.state.validation_issues_created += 1
                self.state.total_issues = len(self.state.issues)

                # Write issue file
                issues_dir = Path(self.config["_issues_dir"])
                issues_dir.mkdir(parents=True, exist_ok=True)
                issue_path = issues_dir / f"{issue.id}.md"
                issue_path.write_text(self._render_fix_issue_md(issue))

            self.logger.info(f"Created {len(new_issues)} fix issue(s)")

            # Implement the fixes
            self._implement_fixes(new_issues)
            save_state(self.state, self.run_dir)

        # Final check
        all_passed, _, _ = self._run_test_tiers()
        if all_passed:
            self.logger.info("Validation complete: all tests passing")
            return True

        self.logger.warning(
            f"Validation incomplete after {self.state.validation_cycles} cycles. "
            f"Some tests still failing."
        )
        return False

    def _run_test_tiers(self) -> tuple[bool, list, list]:
        """Run test tiers progressively. Returns (all_passed, failures, tier_results)."""
        all_failures = []
        tier_results = []
        tiers = ["build", "unit", "integration", "e2e"]

        for tier in tiers:
            command = self.test_profile.get(tier)
            if not command:
                continue

            self.logger.info(f"  Running {tier} tests: {command}")
            passed, output = self._run_command(command)

            tier_results.append({
                "tier": tier,
                "command": command,
                "passed": passed,
            })

            if not passed:
                failures = parse_test_failures(output) if output else []
                if not failures:
                    excerpt = (output or "").strip()
                    if excerpt:
                        excerpt = excerpt[-500:]
                    else:
                        excerpt = "Command exited non-zero with no output."
                    failures = [FailureReport(
                        test_name=f"{tier} command failed",
                        assertion=f"Command `{command}` failed",
                        stack_trace=excerpt,
                        framework="generic",
                    )]
                all_failures.extend(failures)
                self.logger.info(f"  {tier}: FAILED ({len(failures)} failures parsed)")

                # For progressive mode, stop on first failing tier
                if self.test_profile_mode == "progressive":
                    break
            else:
                self.logger.info(f"  {tier}: PASSED")

        all_passed = all(r["passed"] for r in tier_results) if tier_results else True
        return all_passed, all_failures, tier_results

    def _run_command(self, command: str) -> tuple[bool, str]:
        """Run a test command and return (passed, output)."""
        t0 = time.time()
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
                timeout=self.test_timeout,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Test command timed out after {self.test_timeout}s: {command}")
            return False, "Test timed out"
        except FileNotFoundError:
            self.logger.warning(f"Test command not found: {command}")
            return False, ""
        finally:
            add_console_time(self.state, t0)

    def _implement_fixes(self, issues: list[Issue]):
        """Implement fix issues using the same implementer pattern."""
        from .implementer import Implementer

        # Temporarily set these issues as the only pending ones
        # by marking them in state
        for issue in issues:
            issue.status = IssueStatus.PENDING
            self.state.update_issue(issue)

        save_state(self.state, self.run_dir)

        # Create a mini implementer for just these fixes
        implementer = Implementer(
            self.state, self.run_dir, self.config,
            self.cli, self.project_context, self.logger,
        )

        # Implement each fix issue
        for issue in issues:
            pending = self.state.get_issue(issue.id)
            if pending and pending.status == IssueStatus.PENDING:
                self.logger.info(f"  Fixing: {issue.id} — {issue.title[:60]}")
                implementer._implement_issue(pending)
                save_state(self.state, self.run_dir)

    def _render_fix_issue_md(self, issue: Issue) -> str:
        """Render a fix issue as markdown."""
        lines = [
            f"# {issue.id}: {issue.title}",
            "",
            f"**Priority**: {issue.priority}",
            f"**Labels**: {', '.join(issue.labels)}",
            f"**Status**: {issue.status.value}",
            "",
            "## Description",
            "",
            issue.description,
            "",
            "## Acceptance Criteria",
            "",
        ]
        for ac in issue.acceptance_criteria:
            lines.append(f"- [ ] {ac}")
        return "\n".join(lines)
