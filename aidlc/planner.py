"""Planning engine for AIDLC.

Runs time-constrained planning sessions that:
1. Scan repo docs to build project context
2. Assess what planning work needs to be done
3. Have Claude create issues with full specs and acceptance criteria
4. Loop until time budget exhausted or planning frontier is clear
"""

import json
import time
from pathlib import Path

from .models import RunState, RunPhase, Issue, IssueStatus
from .schemas import (
    PlanningOutput, PlanningAction, parse_planning_output,
    PLANNING_SCHEMA_DESCRIPTION,
)
from .claude_cli import ClaudeCLI
from .state_manager import save_state, checkpoint
from .reporting import generate_checkpoint_summary
from .logger import log_checkpoint


class Planner:
    """Runs the planning phase of an AIDLC session."""

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
        self.strict_mode = bool(
            config.get("strict_mode", False)
            or config.get("strict_planning_validation", False)
        )

    def run(self) -> None:
        """Run the full planning loop until budget exhausted or frontier clear."""
        checkpoint_interval = self.config.get("checkpoint_interval_minutes", 15) * 60
        last_checkpoint_time = time.time()
        max_consecutive_failures = self.config.get("max_consecutive_failures", 3)
        consecutive_failures = 0
        finalization_pct = self.config.get("finalization_budget_percent", 10)
        finalization_threshold = 1.0 - (finalization_pct / 100.0)

        # Diminishing returns tracking
        recent_new_issues = []  # Track new issue counts per cycle (last N cycles)
        diminishing_returns_window = self.config.get("diminishing_returns_window", 5)
        diminishing_returns_threshold = self.config.get("diminishing_returns_threshold", 3)

        # Dry-run cycle cap
        max_cycles = self.config.get("max_planning_cycles", 0)
        if self.config.get("dry_run") and max_cycles == 0:
            max_cycles = 3

        self.state.phase = RunPhase.PLANNING
        save_state(self.state, self.run_dir)
        self.logger.info("Starting planning phase")
        self.logger.info(f"  Budget: {self.state.plan_budget_seconds / 3600:.1f}h")

        while True:
            # Budget check
            if self.state.is_plan_budget_exhausted():
                self.state.stop_reason = "Planning budget exhausted"
                self.logger.info("Planning budget exhausted.")
                break

            # Cycle cap
            if max_cycles and self.state.planning_cycles >= max_cycles:
                self.state.stop_reason = f"Max planning cycles ({max_cycles})"
                self.logger.info(f"Max planning cycles reached ({max_cycles}).")
                break

            # Finalization transition
            if (
                self.state.plan_elapsed_seconds >= self.state.plan_budget_seconds * finalization_threshold
                and self.state.phase != RunPhase.PLAN_FINALIZATION
            ):
                self.state.phase = RunPhase.PLAN_FINALIZATION
                self.logger.info(f"Entering planning finalization ({finalization_pct}% budget remaining)")
                save_state(self.state, self.run_dir)

            # Run one planning cycle
            issues_before = self.state.issues_created
            result = self._planning_cycle()

            if result is None:
                self.state.stop_reason = "Planning frontier is clear"
                self.logger.info("No more planning work identified.")
                break
            elif result == "complete":
                # Claude explicitly declared planning complete
                break
            elif result:
                consecutive_failures = 0

                # Track new issues created this cycle for diminishing returns
                new_this_cycle = self.state.issues_created - issues_before
                recent_new_issues.append(new_this_cycle)
                if len(recent_new_issues) > diminishing_returns_window:
                    recent_new_issues.pop(0)

                # Diminishing returns check: if the last N cycles produced
                # zero new issues (only updates), planning is winding down
                if (
                    len(recent_new_issues) >= diminishing_returns_threshold
                    and all(n == 0 for n in recent_new_issues[-diminishing_returns_threshold:])
                    and self.state.issues_created > 0
                ):
                    self.state.stop_reason = (
                        f"Planning complete — {diminishing_returns_threshold} consecutive "
                        f"cycles with no new issues (only refinements)"
                    )
                    self.logger.info(
                        f"Diminishing returns detected: {diminishing_returns_threshold} cycles "
                        f"with no new issues. Planning is complete."
                    )
                    break
            else:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    self.state.stop_reason = f"{max_consecutive_failures} consecutive planning failures"
                    self.logger.error("Too many consecutive failures. Stopping planning.")
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

        save_state(self.state, self.run_dir)

    def _planning_cycle(self) -> bool | None | str:
        """Execute one planning cycle.

        Returns True (success), False (failure), None (frontier clear),
        or "complete" (Claude declared planning done).
        """
        self.state.planning_cycles += 1
        cycle_num = self.state.planning_cycles
        is_finalization = self.state.phase == RunPhase.PLAN_FINALIZATION

        self.logger.info(
            f"=== Planning Cycle {cycle_num} {'(FINALIZATION)' if is_finalization else ''} ==="
        )

        # Build the planning prompt
        prompt = self._build_prompt(is_finalization)
        self.logger.debug(f"Prompt size: {len(prompt)} chars")

        # Execute Claude
        start_time = time.time()
        result = self.cli.execute_prompt(prompt, self.project_root)
        duration = time.time() - start_time
        self.state.plan_elapsed_seconds += duration
        self.state.elapsed_seconds += duration

        # Save raw output
        output_text = result.get("output", "")
        if output_text:
            output_dir = self.run_dir / "claude_outputs"
            output_dir.mkdir(exist_ok=True)
            (output_dir / f"plan_cycle_{cycle_num:04d}.md").write_text(output_text)

        if not result["success"]:
            self.logger.error(f"Cycle {cycle_num} failed: {result.get('error')}")
            return False

        # Parse output
        if self.config.get("dry_run"):
            planning_output = PlanningOutput(
                frontier_assessment=f"[DRY RUN] Cycle {cycle_num}",
                actions=[],
                cycle_notes="Dry run",
            )
        else:
            try:
                planning_output = parse_planning_output(output_text)
            except ValueError as e:
                self.logger.error(f"Failed to parse cycle {cycle_num}: {e}")
                return False

        # Validate
        known_ids = {d["id"] for d in self.state.issues}
        validation_errors = planning_output.validate(
            is_finalization=is_finalization,
            known_issue_ids=known_ids,
        )
        if validation_errors:
            for err in validation_errors:
                self.logger.warning(f"Validation: {err}")
            if self.strict_mode:
                self.logger.error(
                    f"Strict mode: failing cycle {cycle_num} due to "
                    f"{len(validation_errors)} validation error(s)"
                )
                return False

        # Check if Claude declared planning complete
        if planning_output.planning_complete:
            reason = planning_output.completion_reason or "Claude declared planning complete"
            self.state.stop_reason = f"Planning complete — {reason}"
            self.logger.info(f"Planning declared complete: {reason}")

            # Still apply any final actions that came with the completion signal
            if planning_output.actions:
                self.logger.info(f"Applying {len(planning_output.actions)} final actions...")
                for action in planning_output.actions:
                    errors = action.validate(is_finalization=is_finalization, known_issue_ids=known_ids)
                    if not errors:
                        try:
                            self._apply_action(action)
                            if action.issue_id:
                                known_ids.add(action.issue_id)
                        except Exception as e:
                            self.logger.warning(f"Failed to apply final action: {e}")

            return "complete"

        if not planning_output.actions:
            self.logger.info("No actions proposed — frontier may be clear")
            return None

        self.logger.info(f"Cycle {cycle_num}: {len(planning_output.actions)} actions proposed")

        # Apply actions
        applied = 0
        action_errors = []
        for action in planning_output.actions:
            errors = action.validate(is_finalization=is_finalization, known_issue_ids=known_ids)
            if errors:
                self.logger.warning(f"Skipping invalid action: {errors}")
                if self.strict_mode:
                    self.logger.error(
                        f"Strict mode: failing cycle {cycle_num} due to invalid action"
                    )
                    return False
                continue

            try:
                self._apply_action(action)
                applied += 1
                # Update known IDs for subsequent actions in same batch
                if action.issue_id:
                    known_ids.add(action.issue_id)
            except Exception as e:
                self.logger.error(f"Failed to apply action: {e}")
                action_errors.append(str(e))
                if self.strict_mode:
                    self.logger.error(
                        f"Strict mode: failing cycle {cycle_num} after action apply error"
                    )
                    return False

        if action_errors and applied == 0:
            self.logger.error(
                f"Cycle {cycle_num} failed: all actions errored and none were applied"
            )
            return False

        self.logger.info(f"Cycle {cycle_num} complete: {applied} actions applied")
        return True

    def _build_prompt(self, is_finalization: bool) -> str:
        """Build the planning prompt with full project context."""
        sections = []

        # Project context from scanner
        sections.append("# Project Context\n")
        sections.append(self.project_context)

        # Current issue universe
        if self.state.issues:
            sections.append("\n## Current Issue Universe\n")
            for d in self.state.issues:
                issue = Issue.from_dict(d)
                deps = f" (deps: {', '.join(issue.dependencies)})" if issue.dependencies else ""
                sections.append(
                    f"- **{issue.id}**: {issue.title} [{issue.priority}]{deps}"
                )
                if issue.acceptance_criteria:
                    for ac in issue.acceptance_criteria:
                        sections.append(f"  - AC: {ac}")

        # Run state
        plan_h = self.state.plan_elapsed_seconds / 3600
        budget_h = self.state.plan_budget_seconds / 3600
        sections.append(f"\n## Run State\n")
        sections.append(f"- Phase: {self.state.phase.value}")
        sections.append(f"- Planning cycle: {self.state.planning_cycles}")
        sections.append(f"- Elapsed: {plan_h:.1f}h / {budget_h:.0f}h budget")
        sections.append(f"- Issues created: {self.state.issues_created}")
        sections.append(f"- Docs created: {self.state.files_created}")

        # Instructions
        if is_finalization:
            sections.append(self._finalization_instructions())
        else:
            sections.append(self._planning_instructions())

        # Output schema
        sections.append(PLANNING_SCHEMA_DESCRIPTION)

        return "\n\n".join(sections)

    def _planning_instructions(self) -> str:
        return """## Instructions — Planning Mode

You are an autonomous planning agent analyzing this project. Your job is to create a comprehensive
implementation plan as a set of well-specified issues.

**What you should do:**
- Create issues for features, enhancements, bug fixes, refactoring, or infrastructure work
- Each issue must have clear acceptance criteria that are specific and testable
- Set appropriate priority levels (high = blocking/critical, medium = important, low = nice-to-have)
- Define dependency chains — which issues must be completed before others
- Create design docs for complex features that need architectural decisions
- Ensure complete coverage — every piece of planned work should have an issue

**What you should NOT do:**
- Write implementation code (that comes in the implementation phase)
- Create duplicate issues
- Create vague issues without testable acceptance criteria
- Ignore existing documentation — build on what's already planned

**Priority order:**
1. Core infrastructure and foundational issues (high priority, no deps)
2. Main features that depend on infrastructure
3. Secondary features and enhancements
4. Polish, optimization, and documentation

Produce 1-15 high-quality actions per cycle. Quality over quantity.

**When to declare planning complete:**
- Set "planning_complete": true when all work from the docs has been captured as issues
- The time budget is a MAXIMUM, not a target — finishing early with a complete plan is ideal
- Do NOT keep cycling just to make minor refinements to existing issues
- If you find yourself only updating existing issues with no new work to create, planning is done"""

    def _finalization_instructions(self) -> str:
        return """## Instructions — PLANNING FINALIZATION

The planning budget is nearly exhausted. Finalize the plan.

**What you MUST do:**
1. Review all created issues for completeness
2. Ensure acceptance criteria are specific and testable
3. Verify dependency chains are correct and complete
4. Fill any critical gaps in coverage
5. Update any issues that are too vague

**What you MUST NOT do:**
- Create new issues unless they fill a critical gap
- Expand project scope
- Add nice-to-have features

Produce only refinement and gap-filling actions.

**When to declare planning complete:**
- Set "planning_complete": true once all issues are well-specified and no gaps remain
- This is the finalization phase — wrapping up is the goal, not finding more work"""

    def _apply_action(self, action: PlanningAction) -> None:
        """Apply a single planning action."""
        if action.action_type == "create_issue":
            issue = Issue(
                id=action.issue_id,
                title=action.title,
                description=action.description or "",
                priority=action.priority or "medium",
                labels=action.labels,
                dependencies=action.dependencies,
                acceptance_criteria=action.acceptance_criteria,
            )
            self.state.update_issue(issue)
            self.state.issues_created += 1
            self.state.total_issues = len(self.state.issues)

            # Write issue file to .aidlc/issues/
            issues_dir = Path(self.config["_issues_dir"])
            issues_dir.mkdir(parents=True, exist_ok=True)
            issue_path = issues_dir / f"{action.issue_id}.md"
            issue_content = self._render_issue_md(issue)
            issue_path.write_text(issue_content)

            self.logger.info(f"Created issue: {action.issue_id} — {action.title}")

        elif action.action_type == "update_issue":
            existing = self.state.get_issue(action.issue_id)
            if existing:
                if action.description:
                    existing.description = action.description
                if action.priority:
                    existing.priority = action.priority
                if action.labels:
                    existing.labels = action.labels
                if action.acceptance_criteria:
                    existing.acceptance_criteria = action.acceptance_criteria
                if action.dependencies:
                    existing.dependencies = action.dependencies
                self.state.update_issue(existing)

                # Update issue file
                issues_dir = Path(self.config["_issues_dir"])
                issue_path = issues_dir / f"{action.issue_id}.md"
                issue_path.write_text(self._render_issue_md(existing))

                self.logger.info(f"Updated issue: {action.issue_id}")
            else:
                self.logger.warning(f"Cannot update unknown issue: {action.issue_id}")

        elif action.action_type in ("create_doc", "update_doc"):
            file_path = self.project_root / action.file_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(action.content)
            self.state.files_created += 1
            self.state.created_artifacts.append({
                "path": action.file_path,
                "type": "doc",
                "action": "create" if action.action_type == "create_doc" else "update",
            })
            self.logger.info(f"{'Created' if action.action_type == 'create_doc' else 'Updated'} doc: {action.file_path}")

    def _render_issue_md(self, issue: Issue) -> str:
        """Render an issue as markdown."""
        lines = [
            f"# {issue.id}: {issue.title}",
            "",
            f"**Priority**: {issue.priority}",
            f"**Labels**: {', '.join(issue.labels) if issue.labels else 'none'}",
            f"**Dependencies**: {', '.join(issue.dependencies) if issue.dependencies else 'none'}",
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

        if issue.implementation_notes:
            lines.append("")
            lines.append("## Implementation Notes")
            lines.append("")
            lines.append(issue.implementation_notes)

        return "\n".join(lines)
