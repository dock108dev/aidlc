"""Implementation engine for AIDLC.

Takes the issues created during planning and implements them one by one
using Claude. Loops until every issue is implemented and verified.

Flow per issue:
1. Check dependencies are met
2. Build implementation prompt with issue spec + project context
3. Claude implements via CLI (with file edit permissions)
4. Parse result, run verification (tests if available)
5. Mark issue as implemented/verified or failed
6. If failed and retries remain, re-queue
7. Continue until all issues resolved
"""

import json
import subprocess
import time
from pathlib import Path

from .models import RunState, RunPhase, Issue, IssueStatus
from .schemas import (
    ImplementationResult, parse_implementation_result,
    IMPLEMENTATION_SCHEMA_DESCRIPTION,
)
from .claude_cli import ClaudeCLI
from .state_manager import save_state, checkpoint
from .reporting import generate_checkpoint_summary
from .logger import log_checkpoint


class Implementer:
    """Runs the implementation phase of an AIDLC session."""

    def __init__(
        self,
        state: RunState,
        run_dir: Path,
        config: dict,
        cli: ClaudeCLI,
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
        self.allow_dependency_bypass = config.get("allow_dependency_bypass", False)
        self.auto_break_dependency_cycles = config.get("auto_break_dependency_cycles", False)
        self.allow_unstructured_success = config.get("allow_unstructured_success", False)

    def run(self) -> None:
        """Run implementation loop until all issues are resolved."""
        checkpoint_interval = self.config.get("checkpoint_interval_minutes", 15) * 60
        last_checkpoint_time = time.time()
        max_consecutive_failures = self.config.get("max_consecutive_failures", 3)
        consecutive_failures = 0
        wall_start = time.time()

        # Detect test command if not configured
        if not self.test_command:
            self.test_command = self._detect_test_command()
            if self.test_command:
                self.logger.info(f"Auto-detected test command: {self.test_command}")

        # Sort issues by priority and dependency order
        if not self._sort_issues():
            self.state.phase = RunPhase.IMPLEMENTING
            self.state.stop_reason = (
                "Dependency cycle detected. Resolve issue dependencies or enable "
                "'auto_break_dependency_cycles' to continue."
            )
            self.logger.error(self.state.stop_reason)
            save_state(self.state, self.run_dir)
            return

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
                self.logger.info(f"Max implementation cycles reached.")
                break

            # Get next issue to work on
            pending = self.state.get_pending_issues()
            if not pending:
                # Check if we're truly stuck (all remaining are blocked/exhausted)
                blocked_count = sum(
                    1 for d in self.state.issues
                    if d.get("status") in ("pending", "blocked", "failed")
                )
                if blocked_count > 0:
                    if self.allow_dependency_bypass:
                        self.logger.warning(
                            f"{blocked_count} issues stuck (blocked or max retries). "
                            "Attempting to unblock by implementing blocked issues anyway."
                        )
                        # Force-unblock: try blocked issues
                        pending = self._get_blocked_issues()
                        if not pending:
                            self.state.stop_reason = "All remaining issues are stuck"
                            break
                    else:
                        self.state.stop_reason = (
                            f"{blocked_count} issues blocked by unmet dependencies. "
                            "Enable 'allow_dependency_bypass' to force progress."
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
            self.state.wall_clock_seconds += time.time() - wall_start
            wall_start = time.time()

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
                            "Resolve dependencies or enable auto cycle breaking."
                        )
                        self.logger.error(self.state.stop_reason)
                        break

            save_state(self.state, self.run_dir)

            # Checkpoint
            if time.time() - last_checkpoint_time >= checkpoint_interval:
                checkpoint(self.state, self.run_dir)
                reports_dir = Path(self.config["_reports_dir"]) / self.state.run_id
                reports_dir.mkdir(parents=True, exist_ok=True)
                generate_checkpoint_summary(self.state, reports_dir)
                log_checkpoint(self.logger, self.state.to_dict())
                last_checkpoint_time = time.time()

        # Final verification pass
        self.logger.info("Running final verification pass...")
        self._verification_pass()

        save_state(self.state, self.run_dir)

    def _implement_issue(self, issue: Issue) -> bool:
        """Implement a single issue. Returns True on success."""
        issue.status = IssueStatus.IN_PROGRESS
        issue.attempt_count += 1
        self.state.current_issue_id = issue.id
        self.state.update_issue(issue)

        # Build prompt
        prompt = self._build_implementation_prompt(issue)
        self.logger.debug(f"Implementation prompt: {len(prompt)} chars")

        # Execute Claude with file edit permissions
        start_time = time.time()
        result = self.cli.execute_prompt(
            prompt,
            self.project_root,
            allow_edits=True,
        )
        duration = time.time() - start_time
        self.state.elapsed_seconds += duration

        # Save raw output
        output_text = result.get("output", "")
        if output_text:
            output_dir = self.run_dir / "claude_outputs"
            output_dir.mkdir(exist_ok=True)
            (output_dir / f"impl_{issue.id}_{issue.attempt_count:02d}.md").write_text(output_text)

        if not result["success"]:
            self.logger.error(f"Implementation of {issue.id} failed: {result.get('error')}")
            issue.status = IssueStatus.FAILED
            issue.implementation_notes += f"\nAttempt {issue.attempt_count} failed: {result.get('error')}"
            self.state.update_issue(issue)
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
                        f"evaluating unstructured implementation fallback"
                    )
                    allow_fallback = self.allow_unstructured_success or bool(self.test_command)
                    impl_result = ImplementationResult(
                        issue_id=issue.id,
                        success=allow_fallback,
                        summary=(
                            "Implementation completed (no structured JSON, but files changed)"
                            if allow_fallback
                            else "Unstructured output with file changes is not accepted by policy"
                        ),
                        files_changed=changed_files,
                        tests_passed=False,
                        notes=(
                            "Accepted fallback due to policy/test verification path."
                            if allow_fallback
                            else "Set allow_unstructured_success=true to permit this fallback."
                        ),
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
                self.logger.warning(
                    f"{issue.id}: unable to verify file changes (git unavailable/timed out)."
                )
            elif not actual_changes and not self.config.get("dry_run"):
                self.logger.warning(
                    f"{issue.id}: marked success but no files changed in working tree. "
                    f"Verify implementation is correct."
                )

            issue.status = IssueStatus.IMPLEMENTED
            issue.files_changed = impl_result.files_changed
            issue.implementation_notes += f"\nAttempt {issue.attempt_count}: {impl_result.summary}"
            self.state.issues_implemented += 1
            self.logger.info(f"Successfully implemented {issue.id} ({len(issue.files_changed)} files changed)")
        else:
            issue.status = IssueStatus.FAILED
            issue.implementation_notes += f"\nAttempt {issue.attempt_count} failed: {impl_result.notes}"
            self.state.issues_failed += 1
            self.logger.warning(f"Failed to implement {issue.id}: {impl_result.notes}")

        self.state.current_issue_id = None
        self.state.update_issue(issue)
        return impl_result.success

    def _build_implementation_prompt(self, issue: Issue) -> str:
        """Build the prompt for implementing a single issue."""
        # Read issue file for full context
        issue_file = Path(self.config["_issues_dir"]) / f"{issue.id}.md"
        issue_content = ""
        if issue_file.exists():
            issue_content = issue_file.read_text()

        # Get completed issues for context
        completed = [
            d for d in self.state.issues
            if d.get("status") in ("implemented", "verified")
        ]

        sections = [
            "# Implementation Task\n",
            f"You are implementing issue **{issue.id}** for this project.",
            "",
            "## Project Context\n",
            self.project_context[:self.max_impl_context_chars],
            "",
            f"## Issue: {issue.id} — {issue.title}\n",
            f"**Priority**: {issue.priority}",
            f"**Labels**: {', '.join(issue.labels) if issue.labels else 'none'}",
            f"**Dependencies**: {', '.join(issue.dependencies) if issue.dependencies else 'none'}",
            "",
        ]

        if issue_content:
            sections.append("### Full Issue Specification\n")
            sections.append(issue_content)
        else:
            sections.append("### Description\n")
            sections.append(issue.description)
            sections.append("\n### Acceptance Criteria\n")
            for ac in issue.acceptance_criteria:
                sections.append(f"- {ac}")

        # Previous attempts
        if issue.attempt_count > 1:
            sections.append("\n### Previous Attempt Notes\n")
            sections.append(issue.implementation_notes)
            sections.append("\n**Fix the issues from the previous attempt.**")

        # What's already been implemented
        if completed:
            sections.append(f"\n## Already Implemented ({len(completed)} issues)\n")
            for d in completed[:20]:
                sections.append(f"- {d['id']}: {d['title']}")

        # Instructions
        sections.append(self._implementation_instructions())

        # Output schema
        sections.append(IMPLEMENTATION_SCHEMA_DESCRIPTION)

        return "\n\n".join(sections)

    def _implementation_instructions(self) -> str:
        test_instruction = ""
        if self.test_command:
            test_instruction = f"""
- Run tests with: `{self.test_command}`
- All tests must pass before marking as complete
- If tests fail, fix the issues"""

        return f"""## Instructions — Implementation

You are implementing this issue. Your goal is to write production-ready code.

**Requirements:**
- Implement exactly what the issue describes — no more, no less
- Follow the project's existing code style and patterns
- Write clean, well-structured code
- Add appropriate error handling
- Create or update tests for the changes{test_instruction}
- Do NOT modify files unrelated to this issue
- Do NOT introduce breaking changes to existing functionality

**Acceptance criteria must ALL be met.** Check each one.

After implementation, output the structured JSON result.
If you cannot fully implement the issue, set success to false and explain why in notes."""

    def _fix_failing_tests(self, issue: Issue) -> bool:
        """Give Claude a chance to fix failing tests."""
        self.logger.info(f"Attempting to fix failing tests for {issue.id}")

        # Get test output
        test_output = self._run_tests(capture_output=True)

        fix_prompt = f"""# Fix Failing Tests

Tests are failing after implementing issue {issue.id}: {issue.title}

## Test Output

```
{test_output[:5000]}
```

## Instructions

Fix the failing tests. The implementation should match the acceptance criteria:
{chr(10).join(f'- {ac}' for ac in issue.acceptance_criteria)}

Fix the code or tests so everything passes. Do not remove or skip tests.

{IMPLEMENTATION_SCHEMA_DESCRIPTION}
"""

        result = self.cli.execute_prompt(fix_prompt, self.project_root, allow_edits=True)
        if result["success"]:
            self.state.elapsed_seconds += result.get("duration_seconds", 0)
            return self._run_tests()
        return False

    def _run_tests(self, capture_output: bool = False) -> bool | str:
        """Run the project's test suite.

        If capture_output is True, returns the output string instead of bool.
        """
        if not self.test_command:
            return True if not capture_output else ""

        if self.config.get("dry_run"):
            return True if not capture_output else "[DRY RUN] Tests passed"

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

    def _verification_pass(self) -> None:
        """Final pass to verify all implemented issues."""
        self.state.phase = RunPhase.VERIFYING

        for d in self.state.issues:
            if d.get("status") == "implemented":
                issue = Issue.from_dict(d)
                # Mark as verified (tests already passed during implementation)
                issue.status = IssueStatus.VERIFIED
                self.state.update_issue(issue)
                self.state.issues_verified += 1

        # Run full test suite one last time
        if self.test_command:
            self.logger.info("Running final test suite...")
            tests_pass = self._run_tests()
            if tests_pass:
                self.logger.info("All tests pass.")
            else:
                self.logger.warning("Final test suite has failures.")
                self.state.validation_results.append("Final test suite has failures")

    def _sort_issues(self) -> bool:
        """Sort issues by priority and dependency order (topological).

        Detects circular dependencies and logs explicit warnings.
        Issues in cycles have their circular deps removed so they can proceed.
        """
        priority_order = {"high": 0, "medium": 1, "low": 2}

        # Build dependency graph
        id_to_issue = {d["id"]: d for d in self.state.issues}
        sorted_ids = []
        visited = set()
        temp_visited = set()
        cycle_members = set()

        def visit(issue_id: str, path: list[str]) -> None:
            if issue_id in visited:
                return
            if issue_id in temp_visited:
                # Found a cycle — log it explicitly
                cycle_start = path.index(issue_id)
                cycle = path[cycle_start:] + [issue_id]
                cycle_str = " -> ".join(cycle)
                self.logger.error(
                    f"Circular dependency detected: {cycle_str}. "
                    f"{'Breaking cycle automatically.' if self.auto_break_dependency_cycles else 'Manual resolution required.'}"
                )
                for cid in cycle[:-1]:
                    cycle_members.add(cid)
                return
            temp_visited.add(issue_id)
            issue = id_to_issue.get(issue_id, {})
            for dep in issue.get("dependencies", []):
                if dep in id_to_issue:
                    visit(dep, path + [issue_id])
            temp_visited.discard(issue_id)
            visited.add(issue_id)
            sorted_ids.append(issue_id)

        # Visit in priority order
        priority_sorted = sorted(
            self.state.issues,
            key=lambda d: priority_order.get(d.get("priority", "medium"), 1),
        )
        for d in priority_sorted:
            visit(d["id"], [])

        # Handle circular deps from affected issues
        if cycle_members:
            if not self.auto_break_dependency_cycles:
                self.logger.error(
                    f"{len(cycle_members)} issues involved in dependency cycles: "
                    f"{', '.join(sorted(cycle_members))}. Refusing to auto-remove dependencies."
                )
                return False
            self.logger.warning(
                f"{len(cycle_members)} issues involved in dependency cycles: "
                f"{', '.join(sorted(cycle_members))}. Circular deps removed due to config."
            )
            for iid in cycle_members:
                if iid in id_to_issue:
                    issue_data = id_to_issue[iid]
                    issue_data["dependencies"] = [
                        dep for dep in issue_data.get("dependencies", [])
                        if dep not in cycle_members
                    ]

        # Rebuild issues list in sorted order
        new_issues = []
        for iid in sorted_ids:
            if iid in id_to_issue:
                new_issues.append(id_to_issue[iid])
        self.state.issues = new_issues
        return True

    def _get_blocked_issues(self) -> list[Issue]:
        """Get issues that are blocked (deps not met) for force-unblock.

        This is a last resort — only called when all pending issues are stuck.
        Logs explicit warnings about which deps are being bypassed.
        """
        blocked = []
        done_ids = {
            d["id"] for d in self.state.issues
            if d.get("status") in ("implemented", "verified")
        }
        for d in self.state.issues:
            if d.get("status") in ("pending", "blocked"):
                issue = Issue.from_dict(d)
                if issue.attempt_count < issue.max_attempts:
                    unmet = [dep for dep in issue.dependencies if dep not in done_ids]
                    if unmet:
                        self.logger.warning(
                            f"Force-unblocking {issue.id}: bypassing unmet deps {unmet}"
                        )
                    blocked.append(issue)
        return blocked[:1]  # Try one at a time

    def _get_changed_files(self, with_status: bool = False) -> list[str] | tuple[list[str], bool]:
        """Get list of files changed in the working tree (unstaged + staged) via git."""
        detection_ok = True
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                files = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]
                return (files, True) if with_status else files
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            detection_ok = False
            self.logger.warning(f"Unable to run git diff for change detection: {e}")
        # Also check untracked files
        try:
            proc = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                files = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]
                return (files, True) if with_status else files
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            detection_ok = False
            self.logger.warning(f"Unable to run git ls-files for change detection: {e}")
        return ([], detection_ok) if with_status else []

    def _detect_test_command(self) -> str | None:
        """Auto-detect the test command for this project."""
        root = self.project_root

        # Python
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            if (root / "pytest.ini").exists() or (root / "conftest.py").exists():
                return "python -m pytest"
            if (root / "tests").is_dir() or (root / "test").is_dir():
                return "python -m pytest"

        # Node.js
        pkg_json = root / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                scripts = pkg.get("scripts", {})
                if "test" in scripts:
                    return "npm test"
            except (json.JSONDecodeError, OSError):
                pass

        # Rust
        if (root / "Cargo.toml").exists():
            return "cargo test"

        # Go
        if (root / "go.mod").exists():
            return "go test ./..."

        # Ruby
        if (root / "Gemfile").exists():
            if (root / "spec").is_dir():
                return "bundle exec rspec"

        # Make
        if (root / "Makefile").exists():
            try:
                content = (root / "Makefile").read_text()
                if "test:" in content:
                    return "make test"
            except OSError:
                pass

        return None
