"""Planning engine for AIDLC.

Runs time-constrained planning sessions that:
1. Scan repo docs to build project context
2. Assess what planning work needs to be done
3. Have the routed AI provider create issues with full specs and acceptance criteria
4. Loop until time budget exhausted or planning frontier is clear
"""

import time
import uuid
from pathlib import Path

from . import planner_actions, planner_cycle, planner_debug
from .logger import log_checkpoint
from .models import Issue, RunPhase, RunState
from .planner_helpers import (
    build_prompt,
    load_last_cycle_notes,
    render_issue_md,
    save_cycle_notes,
    write_planning_index,
)
from .planner_text import (
    FACETS,
    FINALIZATION_INSTRUCTIONS,
    PLANNING_INSTRUCTIONS,
    VERIFY_INSTRUCTIONS,
    Facet,
    planning_instructions_faceted,
)
from .reporting import generate_checkpoint_summary
from .schemas import (
    PlanningAction,
    PlanningOutput,
)
from .state_manager import checkpoint, save_cycle_snapshot, save_state


class Planner:
    """Runs the planning phase of an AIDLC session."""

    def __init__(
        self,
        state: RunState,
        run_dir: Path,
        config: dict,
        cli,
        project_context: str,
        logger,
        doc_gaps: list | None = None,
        doc_files: list | None = None,
        existing_issues: list | None = None,
    ):
        self.state = state
        self.run_dir = run_dir
        self.config = config
        self.cli = cli
        self.project_context = project_context
        self.doc_gaps = doc_gaps or []
        self.doc_files = doc_files or []
        self.existing_issues = existing_issues or []
        self._last_cycle_notes = load_last_cycle_notes(self.run_dir)
        self.logger = logger
        self.project_root = Path(config["_project_root"])

        # Faceted planning. After the general pass produces a quiet cycle,
        # iterate one cycle per facet (each scoped to a single product lens
        # via planning_instructions_faceted). Verify only fires once the
        # facet sequence is exhausted. Disabled → loop reverts to the
        # legacy single-quiet-cycle-then-verify behavior.
        facets_enabled = bool(config.get("planning_facets_enabled", True))
        self._facets_enabled = facets_enabled
        self._facets_remaining: list[Facet] = list(FACETS) if facets_enabled else []
        self._current_facet: Facet | None = None
        self._general_pass_done: bool = False

    def run(self) -> None:
        """Run the full planning loop until budget exhausted or frontier clear."""
        checkpoint_interval = self.config.get("checkpoint_interval_minutes", 45) * 60
        last_checkpoint_time = time.time()
        max_consecutive_failures = self.config.get("max_consecutive_failures", 3)
        consecutive_failures = 0
        finalization_pct = self.config.get("finalization_budget_percent", 10)
        finalization_threshold = 1.0 - (finalization_pct / 100.0)
        finalization_grace_cycles = max(
            0, int(self.config.get("planning_finalization_grace_cycles", 1))
        )
        finalization_grace_used = 0

        self._pending_completion_reason = None
        # Verify fires after every cycle that files **no new issues** (empty
        # actions or updates-only). `_verify_mode` is True only on the cycle
        # whose prompt includes VERIFY_INSTRUCTIONS. If that cycle also
        # produces no new issues, planning is complete. If verify surfaces
        # gaps (new `create_issue` actions), we clear `_verify_mode` and
        # return to normal planning — the *next* 0-new cycle schedules
        # verify again until a verify pass returns no new work.
        self._verify_mode = False

        if self.config.get("claude_planning_cli_threading", True):
            changed = False
            if not self.state.planning_claude_session_id:
                self.state.planning_claude_session_id = str(uuid.uuid4())
                changed = True
            if not self.state.planning_copilot_session_id:
                self.state.planning_copilot_session_id = str(uuid.uuid4())
                changed = True
            if changed:
                save_state(self.state, self.run_dir)

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
                and self.state.plan_elapsed_seconds
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
            issues_before = self.state.issues_created
            result = self._planning_cycle()
            new_this_cycle = self.state.issues_created - issues_before

            if result is False:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    self.state.stop_reason = (
                        f"{max_consecutive_failures} consecutive planning failures"
                    )
                    self.logger.error("Too many consecutive failures. Stopping planning.")
                    break
                continue
            consecutive_failures = 0

            if new_this_cycle == 0:
                # No new issues this cycle — no actions at all OR only
                # update_issue actions. Decide what runs next:
                #   1. We just finished a verify cycle (0 new) → coverage
                #      confirmed; planning complete.
                #   2. Faceted planning is enabled and the general pass just
                #      ran with 0 new → start the facet sequence.
                #   3. We're in the facet sequence and the current facet
                #      produced 0 new → advance to the next facet.
                #   4. Facet sequence exhausted (or facets disabled) → next
                #      cycle is verify.
                if self._verify_mode:
                    reason = (
                        self._pending_completion_reason
                        or "Verify cycle confirmed coverage: no new issues needed."
                    )
                    self.state.stop_reason = reason
                    self.logger.info(f"Planning complete: {reason}")
                    self._verify_mode = False
                    break
                if self._advance_facet_sequence():
                    # _advance_facet_sequence() set _current_facet and logged
                    # the transition; no verify yet.
                    pass
                else:
                    self._verify_mode = True
                    self.logger.info(
                        "No new issues this cycle — switching to verify mode for the "
                        "next cycle. The verify prompt walks through BRAINDUMP + "
                        "discovery findings + existing issues to confirm coverage or "
                        "surface missing pieces."
                    )
            else:
                # Cycle produced new issues.
                if self._verify_mode:
                    # Verify surfaced real gaps — file them, exit verify mode,
                    # resume normal planning. The next 0-new cycle schedules
                    # verify again (or another facet pass if facets aren't
                    # exhausted, but they are by the time we reach verify).
                    self.logger.info(
                        f"Verify cycle surfaced {new_this_cycle} new issue(s) — filed; "
                        "returning to normal planning. The next cycle with no new issues "
                        "will enter verify again."
                    )
                    self._verify_mode = False
                elif self._current_facet is not None:
                    # Productive facet cycle: advance to the next facet (or
                    # exit the facet sequence). Facet cycles are one-shot;
                    # if the model surfaces more facet-specific work next
                    # session, the next quiet cycle will re-enter verify.
                    finished = self._current_facet
                    self._current_facet = None
                    self.logger.info(
                        f"Facet '{finished.name}' surfaced {new_this_cycle} action(s); "
                        "advancing to next facet."
                    )
                    self._advance_facet_sequence()
                elif not self._general_pass_done and self._facets_enabled:
                    # General pass produced new issues (this is the common
                    # cycle-1 path) — keep running general until it goes
                    # quiet, then start the facet sequence. No-op here; the
                    # quiet branch above handles the transition.
                    pass
                # Honor an explicit planning_complete from a productive verify
                # cycle (model said "I filed N issues and now we're done").
                if self._pending_completion_reason:
                    self.state.stop_reason = self._pending_completion_reason
                    self.logger.info(
                        f"Planning complete (model self-declared): "
                        f"{self._pending_completion_reason}"
                    )
                    self._verify_mode = False
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
        return planner_cycle.planning_cycle(self)

    def _write_planning_index(self):
        return write_planning_index(self)

    def _build_prompt(self, is_finalization: bool) -> str:
        return build_prompt(self, is_finalization)

    def _planning_instructions(self) -> str:
        if self._current_facet is not None:
            return planning_instructions_faceted(self._current_facet)
        return PLANNING_INSTRUCTIONS

    def _advance_facet_sequence(self) -> bool:
        """Advance the facet pipeline by one step on a 0-new-issues cycle.

        Returns True when a facet was scheduled for the next cycle (so the
        caller should NOT switch into verify mode); False when the facet
        sequence is finished (or disabled) and verify is the right next
        step.

        State transitions on a quiet cycle:
          - facets disabled → return False (verify next).
          - general pass not yet marked done → mark it done; pop the first
            facet into ``_current_facet``; return True.
          - already in a facet → pop the next facet; return True.
          - facet list exhausted → clear ``_current_facet``; return False.
        """
        if not self._facets_enabled:
            return False

        if not self._general_pass_done:
            self._general_pass_done = True
            self.logger.info(
                "General planning pass produced no new issues — entering faceted "
                f"sequence ({len(self._facets_remaining)} facets)."
            )

        if not self._facets_remaining:
            if self._current_facet is not None:
                self.logger.info(
                    f"Facet '{self._current_facet.name}' produced no new issues; "
                    "facet sequence complete."
                )
            self._current_facet = None
            return False

        next_facet = self._facets_remaining.pop(0)
        # Total facet count is len(FACETS). Position = total - remaining (after pop).
        total = len(FACETS)
        position = total - len(self._facets_remaining)
        self._current_facet = next_facet
        self.logger.info(f"Entering facet cycle ({position}/{total}): {next_facet.name}")
        return True

    def _finalization_instructions(self) -> str:
        return FINALIZATION_INSTRUCTIONS

    def _save_cycle_notes(self, frontier: str, notes: str, cycle_num: int):
        """Save the current cycle's context for resume."""
        save_cycle_notes(self.run_dir, frontier, notes, cycle_num)

    def _verify_instructions(self) -> str:
        return VERIFY_INSTRUCTIONS

    def _planning_session_continuation(self) -> dict[str, str | None]:
        """Per-provider opaque ids for **fresh-mint** session calls.

        Claude only goes here on cycle 1 (or any cycle where the session is
        not yet resumable); Claude Code rejects ``--session-id`` when the id
        already exists, so once we've successfully completed one cycle we
        switch Claude to the resume path. Codex also resumes captured threads
        through the resume path. Copilot uses its own continuation convention.
        """
        claude_id: str | None = None
        if not self.state.planning_claude_session_resumable:
            claude_id = self.state.planning_claude_session_id
        return {
            "claude": claude_id,
            "openai": None,
            "copilot": self.state.planning_copilot_session_id,
        }

    def _planning_session_resume(self) -> dict[str, str | None]:
        """Per-provider session ids for **resume** calls.

        Claude is populated only after the first successful cycle flipped
        ``planning_claude_session_resumable`` to True. Codex is populated once
        a JSONL ``thread.started`` id has been captured.
        """
        claude_id: str | None = None
        if self.state.planning_claude_session_resumable:
            claude_id = self.state.planning_claude_session_id
        return {
            "claude": claude_id,
            "openai": self.state.planning_openai_thread_id,
            "copilot": None,
        }

    def _apply_action(self, action: PlanningAction) -> None:
        planner_actions.apply_action(self, action)

    def _render_issue_md(self, issue: Issue) -> str:
        """Render an issue as markdown."""
        return render_issue_md(issue)

    def _log_provider_result(self, cycle_num: int, result: dict) -> None:
        """Log which provider/model handled the planning cycle."""
        provider = str(result.get("provider_id") or "unknown")
        model = str(result.get("model_used") or "unknown")
        output_len = len(result.get("output") or "")
        self.logger.info(
            f"  Planning Cycle {cycle_num} model: {provider}/{model} "
            f"({output_len:,} chars returned)"
        )

    def _sanitize_issue_dependencies(self) -> int:
        return planner_actions.sanitize_issue_dependencies(self)

    def _preflight_routing_snapshot(self) -> dict | None:
        return planner_debug.preflight_routing_snapshot(self)

    def _serialize_result_metadata(self, result: dict) -> dict:
        return planner_debug.serialize_result_metadata(result)

    def _write_cycle_debug_bundle(
        self,
        *,
        cycle_num: int,
        prompt_path: Path,
        output_path: Path,
        result: dict,
        preflight_routing: dict | None,
        parse_status: str,
        parse_error: str | None = None,
        planning_output: PlanningOutput | None = None,
    ) -> None:
        planner_debug.write_cycle_debug_bundle(
            self,
            cycle_num=cycle_num,
            prompt_path=prompt_path,
            output_path=output_path,
            result=result,
            preflight_routing=preflight_routing,
            parse_status=parse_status,
            parse_error=parse_error,
            planning_output=planning_output,
        )
