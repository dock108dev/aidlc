"""Planning engine for AIDLC.

Runs time-constrained planning sessions that:
1. Scan repo docs to build project context
2. Assess what planning work needs to be done
3. Have Claude create issues with full specs and acceptance criteria
4. Loop until time budget exhausted or planning frontier is clear
"""

import time
from pathlib import Path

from .models import RunState, RunPhase, Issue
from .schemas import (
    PlanningOutput, PlanningAction, parse_planning_output,
)
from .claude_cli import ClaudeCLI
from .state_manager import save_state, checkpoint, save_cycle_snapshot
from .reporting import generate_checkpoint_summary
from .logger import log_checkpoint
from .planner_helpers import (
    assess_planning_foundation,
    build_prompt,
    execute_research,
    load_last_cycle_notes,
    render_issue_md,
    render_planning_foundation,
    save_cycle_notes,
    upsert_doc_file,
    write_planning_index,
)
from .planner_text import (
    COMPLETION_OFFER_INSTRUCTIONS,
    FINALIZATION_INSTRUCTIONS,
    PLANNING_INSTRUCTIONS,
)


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
        doc_gaps: list | None = None,
        doc_files: list | None = None,
    ):
        self.state = state
        self.run_dir = run_dir
        self.config = config
        self.cli = cli
        self.project_context = project_context
        self.doc_gaps = doc_gaps or []
        self.doc_files = doc_files or []
        self._research_count = 0
        self._last_cycle_notes = load_last_cycle_notes(self.run_dir)
        self.logger = logger
        self.project_root = Path(config["_project_root"])
        self.planning_doc_min_chars = config.get("planning_doc_min_chars", 800)
        self._planning_foundation = self._assess_planning_foundation()

    def run(self) -> None:
        """Run the full planning loop until budget exhausted or frontier clear."""
        checkpoint_interval = self.config.get("checkpoint_interval_minutes", 15) * 60
        last_checkpoint_time = time.time()
        max_consecutive_failures = self.config.get("max_consecutive_failures", 3)
        consecutive_failures = 0
        finalization_pct = self.config.get("finalization_budget_percent", 10)
        finalization_threshold = 1.0 - (finalization_pct / 100.0)
        finalization_grace_cycles = max(
            0, int(self.config.get("planning_finalization_grace_cycles", 1))
        )
        finalization_grace_used = 0

        # Diminishing returns tracking — tracks (new_issues, total_actions) per cycle
        recent_cycles = []  # list of (new_issue_count, total_action_count)
        diminishing_returns_window = self.config.get("diminishing_returns_window", 5)
        diminishing_returns_threshold = self.config.get("diminishing_returns_threshold", 3)
        self._pending_completion_reason = None
        self._offer_completion = False  # When True, prompt tells Claude it can declare done

        # Dry-run cycle cap
        max_cycles = self.config.get("max_planning_cycles", 0)
        if self.config.get("dry_run") and max_cycles == 0:
            max_cycles = 3

        self.state.phase = RunPhase.PLANNING
        save_state(self.state, self.run_dir)
        self.logger.info("Starting planning phase")
        self.logger.info(f"  Budget: {self.state.plan_budget_seconds / 3600:.1f}h")

        while True:
            budget_exhausted = self.state.is_plan_budget_exhausted()
            if (
                not budget_exhausted
                and
                self.state.plan_elapsed_seconds
                >= self.state.plan_budget_seconds * finalization_threshold
                and self.state.phase != RunPhase.PLAN_FINALIZATION
            ):
                self.state.phase = RunPhase.PLAN_FINALIZATION
                self.logger.info(
                    f"Entering planning finalization ({finalization_pct}% budget remaining)"
                )
                save_state(self.state, self.run_dir)

            # Budget check
            if budget_exhausted:
                if (
                    self.state.phase != RunPhase.PLAN_FINALIZATION
                    and finalization_grace_used < finalization_grace_cycles
                ):
                    self.state.phase = RunPhase.PLAN_FINALIZATION
                    finalization_grace_used += 1
                    self.logger.warning(
                        "Planning budget exhausted before finalization; "
                        f"running grace finalization cycle "
                        f"({finalization_grace_used}/{finalization_grace_cycles})"
                    )
                    save_state(self.state, self.run_dir)
                else:
                    self.state.stop_reason = "Planning budget exhausted"
                    self.logger.info("Planning budget exhausted.")
                    break

            # Cycle cap
            if max_cycles and self.state.planning_cycles >= max_cycles:
                self.state.stop_reason = f"Max planning cycles ({max_cycles})"
                self.logger.info(f"Max planning cycles reached ({max_cycles}).")
                break

            # Save cycle snapshot before running (for revert support)
            save_cycle_snapshot(self.state, self.run_dir, self.state.planning_cycles + 1)

            # Run one planning cycle
            self._planning_foundation = self._assess_planning_foundation()
            issues_before = self.state.issues_created
            result = self._planning_cycle()

            if result is None:
                # No actions proposed. Only treat as "done" if:
                # 1. We already offered completion and Claude accepted, OR
                # 2. We've been winding down (multiple empty/update-only cycles)
                if self._pending_completion_reason:
                    self.state.stop_reason = self._pending_completion_reason
                    self.logger.info("No more planning work identified (completion confirmed).")
                    break
                elif getattr(self, "_offer_completion", False):
                    self.state.stop_reason = "Planning frontier is clear"
                    self.logger.info("No more planning work identified.")
                    break
                else:
                    # Early empty cycle — Claude may have stopped prematurely.
                    # Track it as a zero-new-issue cycle for diminishing returns.
                    self.logger.warning(
                        "Empty planning cycle but completion not yet offered — "
                        "continuing to ensure repository scope is fully captured."
                    )
                    recent_cycles.append(0)
                    # Check if we've had enough empty cycles to trigger winding down
                    if (
                        len(recent_cycles) >= diminishing_returns_threshold
                        and self.state.issues_created > 0
                        and self._planning_foundation.get("ready", False)
                        and all(n == 0 for n in recent_cycles[-diminishing_returns_threshold:])
                    ):
                        if not self._offer_completion:
                            self._offer_completion = True
                            self.logger.info("Offering completion option after repeated empty cycles.")
                        else:
                            self.state.stop_reason = "Planning frontier is clear"
                            self.logger.info("Planning complete after repeated empty cycles.")
                            break
            elif result is False:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    self.state.stop_reason = f"{max_consecutive_failures} consecutive planning failures"
                    self.logger.error("Too many consecutive failures. Stopping planning.")
                    break
                continue
            else:
                # Cycle succeeded
                consecutive_failures = 0
                new_this_cycle = self.state.issues_created - issues_before

                # Track cycle stats
                recent_cycles.append(new_this_cycle)
                if len(recent_cycles) > diminishing_returns_window:
                    recent_cycles.pop(0)

                # Check for winding down: last N cycles all had 0 new issues
                if (
                    len(recent_cycles) >= diminishing_returns_threshold
                    and self.state.issues_created > 0
                    and self._planning_foundation.get("ready", False)
                    and all(n == 0 for n in recent_cycles[-diminishing_returns_threshold:])
                ):
                    if not self._offer_completion:
                        # First detection: tell Claude it can declare done next cycle
                        self._offer_completion = True
                        self.logger.info(
                            f"Winding down detected: {diminishing_returns_threshold} cycles "
                            f"with no new issues. Offering completion option to Claude."
                        )
                    elif self._pending_completion_reason:
                        # Claude accepted the offer — honor it
                        self.state.stop_reason = self._pending_completion_reason
                        self.logger.info(f"Planning complete (confirmed): {self._pending_completion_reason}")
                        break
                    else:
                        # Claude didn't declare complete but is still just updating
                        # Give it one more cycle, then force exit
                        tail_len = sum(1 for n in recent_cycles if n == 0)
                        if tail_len >= diminishing_returns_threshold + 2:
                            self.state.stop_reason = (
                                f"Planning complete — {tail_len} consecutive cycles "
                                f"with no new issues"
                            )
                            self.logger.info(
                                f"Forced planning exit: {tail_len} update-only cycles."
                            )
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

    def _planning_cycle(self) -> bool | None:
        """Execute one planning cycle.

        Returns True (success), False (failure), or None (frontier clear).
        If Claude signals planning_complete, it's stored in
        self._pending_completion_reason for the run() loop to evaluate.
        """
        self._cycle_research_count = 0
        self.state.planning_cycles += 1
        cycle_num = self.state.planning_cycles
        is_finalization = self.state.phase == RunPhase.PLAN_FINALIZATION

        self.logger.info(
            f"=== Planning Cycle {cycle_num} {'(FINALIZATION)' if is_finalization else ''} ==="
        )

        # Build the planning prompt
        prompt = self._build_prompt(is_finalization)
        self.logger.debug(f"Prompt size: {len(prompt)} chars")

        # Execute Claude with file access (can read project files + write issues/docs)
        planning_model = self.config.get("claude_model_planning")
        start_time = time.time()
        result = self.cli.execute_prompt(
            prompt, self.project_root, allow_edits=True, model_override=planning_model,
        )
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

        # Validate — pre-register new issue IDs from this batch so
        # within-batch dependencies are allowed (e.g., ISSUE-018 depends on
        # ISSUE-016, both created in the same cycle)
        known_ids = {d["id"] for d in self.state.issues}
        batch_new_ids = {
            a.issue_id for a in planning_output.actions
            if a.action_type == "create_issue" and a.issue_id
        }

        # Batch-level validation uses original known_ids (catches true duplicates)
        validation_errors = planning_output.validate(
            is_finalization=is_finalization,
            known_issue_ids=known_ids,
        )
        if validation_errors:
            for err in validation_errors:
                self.logger.warning(f"Validation: {err}")
            # Don't fail the whole cycle — skip bad actions individually below

        # Save cycle context for resume continuity
        self._save_cycle_notes(
            planning_output.frontier_assessment,
            planning_output.cycle_notes,
            cycle_num,
        )
        self._last_cycle_notes = (
            f"Last planning cycle ({cycle_num}) assessment: "
            f"{planning_output.frontier_assessment}\n"
            f"Notes: {planning_output.cycle_notes}"
        )

        # Only accept planning_complete if we've offered it and planning docs are sufficient.
        # (Claude sometimes adds this field unprompted — ignore it until invited)
        foundation_ready = self._planning_foundation.get("ready", False)
        if (
            planning_output.planning_complete
            and getattr(self, "_offer_completion", False)
            and foundation_ready
        ):
            reason = planning_output.completion_reason or "Claude declared planning complete"
            self._pending_completion_reason = f"Planning complete — {reason}"
            self.logger.info(f"Claude signaled planning_complete (accepted): {reason}")
        elif planning_output.planning_complete:
            if not foundation_ready:
                self.logger.warning(
                    "Claude signaled planning_complete but planning docs are still incomplete "
                    "(missing/thin foundation docs) — ignoring"
                )
            else:
                self.logger.info(
                    "Claude signaled planning_complete but completion not yet offered — ignoring"
                )

        if not planning_output.actions:
            if not foundation_ready:
                self.logger.warning(
                    "No actions proposed but planning foundation is incomplete — "
                    "expecting create_doc/update_doc actions first."
                )
                return False
            self.logger.info("No actions proposed — frontier may be clear")
            return None

        self.logger.info(f"Cycle {cycle_num}: {len(planning_output.actions)} actions proposed")

        # Apply actions
        applied = 0
        action_errors = []
        total_actions = len(planning_output.actions)
        for action in planning_output.actions:
            errors = action.validate(
                is_finalization=is_finalization,
                known_issue_ids=known_ids,
                batch_issue_ids=batch_new_ids,
            )
            if errors:
                self.logger.warning(f"Skipping invalid action: {errors}")
                action_errors.append(errors)
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

        if action_errors and applied == 0:
            self.logger.error(
                f"Cycle {cycle_num} failed: all {len(action_errors)} actions errored"
            )
            return False

        if action_errors and total_actions:
            failure_ratio = len(action_errors) / total_actions
            ratio_threshold = self.config.get("planning_action_failure_ratio_threshold", 0.6)
            if failure_ratio >= ratio_threshold:
                self.logger.error(
                    f"Cycle {cycle_num} failed: action failure ratio {failure_ratio:.0%} "
                    f"exceeds threshold {ratio_threshold:.0%} "
                    f"({len(action_errors)}/{total_actions} actions failed)"
                )
                return False
            self.logger.warning(
                f"Cycle {cycle_num} partial success: {len(action_errors)}/{total_actions} "
                "actions failed but below failure threshold"
            )

        self.logger.info(f"Cycle {cycle_num} complete: {applied} actions applied")
        return True

    def _write_planning_index(self):
        return write_planning_index(self)

    def _build_prompt(self, is_finalization: bool) -> str:
        return build_prompt(self, is_finalization)

    def _planning_instructions(self) -> str:
        return PLANNING_INSTRUCTIONS

    def _finalization_instructions(self) -> str:
        return FINALIZATION_INSTRUCTIONS

    def _save_cycle_notes(self, frontier: str, notes: str, cycle_num: int):
        """Save the current cycle's context for resume."""
        save_cycle_notes(self.run_dir, frontier, notes, cycle_num)

    def _completion_offer_instructions(self) -> str:
        return COMPLETION_OFFER_INSTRUCTIONS

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
            self._upsert_doc_file(action.file_path, action.content)
            self.state.files_created += 1
            self.state.created_artifacts.append({
                "path": action.file_path,
                "type": "doc",
                "action": "create" if action.action_type == "create_doc" else "update",
            })
            self.logger.info(f"{'Created' if action.action_type == 'create_doc' else 'Updated'} doc: {action.file_path}")

        elif action.action_type == "research":
            self._execute_research(action)

    def _execute_research(self, action: PlanningAction) -> None:
        """Execute a research action."""
        execute_research(self, action)

    def _upsert_doc_file(self, rel_path: str, content: str) -> None:
        """Update in-memory doc cache so planning quality checks see new docs immediately."""
        upsert_doc_file(self, rel_path, content)

    def _assess_planning_foundation(self) -> dict:
        """Assess whether core planning docs are present and sufficiently detailed."""
        return assess_planning_foundation(self)

    def _render_planning_foundation(self) -> str:
        return render_planning_foundation(self)

    def _render_issue_md(self, issue: Issue) -> str:
        """Render an issue as markdown."""
        return render_issue_md(issue)
