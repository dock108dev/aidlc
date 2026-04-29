"""Planning engine for AIDLC.

Runs time-constrained planning sessions that:
1. Scan repo docs to build project context
2. Assess what planning work needs to be done
3. Have the routed AI provider create issues with full specs and acceptance criteria
4. Loop until time budget exhausted or planning frontier is clear
"""

import json
import time
from pathlib import Path

from .logger import log_checkpoint
from .models import Issue, RunPhase, RunState
from .planner_dependency_graph import sanitize_dependencies
from .planner_helpers import (
    build_prompt,
    load_last_cycle_notes,
    render_issue_md,
    save_cycle_notes,
    write_planning_index,
)
from .planner_text import (
    FINALIZATION_INSTRUCTIONS,
    PLANNING_INSTRUCTIONS,
    VERIFY_INSTRUCTIONS,
)
from .reporting import generate_checkpoint_summary
from .schemas import (
    PlanningAction,
    PlanningOutput,
    parse_planning_output,
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

        self._pending_completion_reason = None
        # Verify is a *one-shot* coverage check that fires after the first
        # 0-new-issues cycle:
        #   - `_verify_mode`  : True only on the cycle whose prompt is the
        #                       VERIFY_INSTRUCTIONS variant. Cleared as soon
        #                       as the cycle returns.
        #   - `_verify_used`  : sticky for the rest of the run. Once verify
        #                       has fired once, we don't fire it again — if
        #                       the next 0-new cycle happens, planning is
        #                       complete (the gap that verify surfaced has
        #                       since been filed; another empty cycle now
        #                       means we're really done).
        # This replaces the earlier multi-empty-cycle diminishing-returns
        # dance: one explicit coverage check, then trust.
        self._verify_mode = False
        self._verify_used = False

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
                # update_issue actions. Three outcomes by state:
                #   1. Just finished a verify cycle (0 new) → coverage
                #      confirmed; planning complete.
                #   2. Already used the one-shot verify earlier this run →
                #      trust this empty cycle; planning complete. We do
                #      NOT re-verify; the verify has already given the
                #      model its one explicit chance to surface gaps.
                #   3. First 0-new cycle and verify not yet used → switch
                #      to verify mode for the next cycle.
                if self._verify_mode:
                    reason = (
                        self._pending_completion_reason
                        or "Verify cycle confirmed coverage: no new issues needed."
                    )
                    self.state.stop_reason = reason
                    self.logger.info(f"Planning complete: {reason}")
                    break
                elif self._verify_used:
                    self.state.stop_reason = (
                        "Planning complete: verify pass already ran earlier this run "
                        "and this empty cycle confirms no remaining gaps."
                    )
                    self.logger.info(self.state.stop_reason)
                    break
                else:
                    self._verify_mode = True
                    self.logger.info(
                        "No new issues this cycle — switching to verify mode for the next "
                        "cycle. The verify prompt walks through BRAINDUMP + discovery findings "
                        "+ existing issues to confirm coverage or surface missing pieces."
                    )
            else:
                # Cycle produced new issues. If we were in verify mode,
                # that's a real coverage gap verify surfaced — file the
                # issues, exit verify mode, mark verify as used so we
                # do NOT re-verify on a future empty cycle. Normal cycles
                # resume; the next 0-new cycle will be the planning-
                # complete signal directly.
                if self._verify_mode:
                    self.logger.info(
                        f"Verify cycle surfaced {new_this_cycle} new issue(s) — filed; "
                        "returning to normal planning. Verify will not fire again this run; "
                        "the next empty cycle will end planning."
                    )
                    self._verify_mode = False
                    self._verify_used = True
                # Honor an explicit planning_complete from a productive verify
                # cycle (model said "I filed N issues and now we're done").
                if self._pending_completion_reason:
                    self.state.stop_reason = self._pending_completion_reason
                    self.logger.info(
                        f"Planning complete (model self-declared): "
                        f"{self._pending_completion_reason}"
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
        self.state.planning_cycles += 1
        cycle_num = self.state.planning_cycles
        is_finalization = self.state.phase == RunPhase.PLAN_FINALIZATION

        self.logger.info(
            f"=== Planning Cycle {cycle_num} {'(FINALIZATION)' if is_finalization else ''} ==="
        )

        # Build the planning prompt
        prompt = self._build_prompt(is_finalization)
        self.logger.debug(f"Prompt size: {len(prompt)} chars")
        preflight_routing = self._preflight_routing_snapshot()
        if preflight_routing:
            self.logger.info(
                "  Planning Cycle %s selected route: %s/%s",
                cycle_num,
                preflight_routing.get("provider_id"),
                preflight_routing.get("model"),
            )

        # Execute Claude with file access (can read project files + write issues/docs)
        start_time = time.time()
        result = self.cli.execute_prompt(
            prompt,
            self.project_root,
            allow_edits=True,
        )
        self._log_provider_result(cycle_num, result)
        self.state.record_provider_result(result, self.config, phase="planning")
        duration = time.time() - start_time
        self.state.plan_elapsed_seconds += duration
        self.state.elapsed_seconds += duration

        # Save raw output
        output_text = result.get("output", "")
        output_dir = self.run_dir / "claude_outputs"
        output_dir.mkdir(exist_ok=True)
        prompt_path = output_dir / f"plan_cycle_{cycle_num:04d}.prompt.md"
        output_path = output_dir / f"plan_cycle_{cycle_num:04d}.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        if output_text:
            output_path.write_text(output_text, encoding="utf-8")
        else:
            output_path.write_text("", encoding="utf-8")

        if not result["success"]:
            self._write_cycle_debug_bundle(
                cycle_num=cycle_num,
                prompt_path=prompt_path,
                output_path=output_path,
                result=result,
                preflight_routing=preflight_routing,
                parse_status="provider_failure",
                parse_error=result.get("error"),
            )
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
                self._write_cycle_debug_bundle(
                    cycle_num=cycle_num,
                    prompt_path=prompt_path,
                    output_path=output_path,
                    result=result,
                    preflight_routing=preflight_routing,
                    parse_status="parse_error",
                    parse_error=str(e),
                )
                self.logger.error(f"Failed to parse cycle {cycle_num}: {e}")
                preview = (output_text or "").strip().replace("\n", "\\n")
                stderr_preview = str(result.get("raw_stderr") or "").strip().replace("\n", "\\n")
                if preview:
                    self.logger.error(
                        f"Cycle {cycle_num} output preview ({min(len(preview), 240)} chars): "
                        f"{preview[:240]}"
                    )
                elif stderr_preview:
                    self.logger.error(
                        f"Cycle {cycle_num} had empty parsed output; stderr preview "
                        f"({min(len(stderr_preview), 240)} chars): {stderr_preview[:240]}"
                    )
                else:
                    self.logger.error(
                        f"Cycle {cycle_num} returned no parseable output from "
                        f"{result.get('provider_id')}/{result.get('model_used')}"
                    )
                return False

        self._write_cycle_debug_bundle(
            cycle_num=cycle_num,
            prompt_path=prompt_path,
            output_path=output_path,
            result=result,
            preflight_routing=preflight_routing,
            parse_status="ok",
            planning_output=planning_output,
        )

        # Validate — pre-register new issue IDs from this batch so
        # within-batch dependencies are allowed (issue X may depend on
        # issue Y when both are created in the same cycle).
        # Include existing issues from previous runs so dependencies to them are valid.
        # existing_issues are {"path": "...", "content": "..."} dicts — extract the
        # issue ID from the filename stem.
        from pathlib import Path as _Path

        known_ids = {d["id"] for d in self.state.issues} | {
            _Path(e["path"]).stem
            for e in self.existing_issues
            if e.get("path") and _Path(e["path"]).stem.upper().startswith("ISSUE")
        }
        batch_new_ids = {
            a.issue_id
            for a in planning_output.actions
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

        # Accept planning_complete when the model emits it during a verify
        # cycle (where the prompt explicitly invites it) — the verify prompt
        # is the only place the model is told it's allowed to declare done.
        # On normal cycles the field is ignored; the model sometimes adds it
        # unprompted on greenfield repos and we don't want to short-circuit.
        if planning_output.planning_complete and self._verify_mode:
            reason = planning_output.completion_reason or "planning completed"
            self._pending_completion_reason = f"Planning complete — {reason}"
            self.logger.info(
                f"Model signaled planning_complete (accepted in verify mode): {reason}"
            )
        elif planning_output.planning_complete:
            self.logger.info(
                "Model signaled planning_complete on a normal cycle — ignoring "
                "(only accepted in verify mode, which fires after an empty cycle)."
            )

        if not planning_output.actions:
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
            self.logger.error(f"Cycle {cycle_num} failed: all {len(action_errors)} actions errored")
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

        sanitized_changes = self._sanitize_issue_dependencies()
        if sanitized_changes:
            self.logger.info(
                f"Cycle {cycle_num}: normalized dependency graph "
                f"({sanitized_changes} change{'s' if sanitized_changes != 1 else ''})"
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

    def _verify_instructions(self) -> str:
        return VERIFY_INSTRUCTIONS

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
                if action.dependencies is not None:
                    existing.dependencies = action.dependencies
                self.state.update_issue(existing)

                # Update issue file
                issues_dir = Path(self.config["_issues_dir"])
                issue_path = issues_dir / f"{action.issue_id}.md"
                issue_path.write_text(self._render_issue_md(existing))

                self.logger.info(f"Updated issue: {action.issue_id}")
            else:
                self.logger.warning(f"Cannot update unknown issue: {action.issue_id}")

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
        """Normalize dependency graph and persist any changes.

        Delegates the pure logic to ``planner_dependency_graph.sanitize_dependencies``;
        this method handles the write-side effects (markdown sync, state update).
        Returns total number of edge changes applied.
        """
        if not self.state.issues:
            return 0

        id_to_issue = {d["id"]: d for d in self.state.issues if d.get("id")}
        touched, total_changes = sanitize_dependencies(self.state.issues, self.logger)

        if touched:
            issues_dir = Path(self.config["_issues_dir"])
            issues_dir.mkdir(parents=True, exist_ok=True)
            for issue_id in sorted(touched):
                issue_data = id_to_issue.get(issue_id)
                if not issue_data:
                    continue
                issue = Issue.from_dict(issue_data)
                self.state.update_issue(issue)
                issue_path = issues_dir / f"{issue_id}.md"
                issue_path.write_text(self._render_issue_md(issue))
                self.logger.info(f"Updated issue dependencies: {issue_id}")
        return total_changes

    def _preflight_routing_snapshot(self) -> dict | None:
        """Best-effort router preview before the planning call starts."""
        resolve_fn = getattr(type(self.cli), "resolve", None)
        if not callable(resolve_fn):
            return None
        try:
            decision = self.cli.resolve(phase="planning")
        except Exception:
            return None
        provider_id = getattr(decision, "provider_id", None)
        model = getattr(decision, "model", None)
        if not provider_id and not model:
            return None
        return {
            "provider_id": provider_id,
            "account_id": getattr(decision, "account_id", None),
            "model": model,
            "reasoning": getattr(decision, "reasoning", None),
            "strategy_used": getattr(decision, "strategy_used", None),
            "fallback": getattr(decision, "fallback", None),
            "tier": getattr(decision, "tier", None),
            "quality_note": getattr(decision, "quality_note", None),
        }

    def _serialize_result_metadata(self, result: dict) -> dict:
        usage = result.get("usage")
        return {
            "success": bool(result.get("success")),
            "provider_id": result.get("provider_id"),
            "account_id": result.get("account_id"),
            "model_used": result.get("model_used"),
            "error": result.get("error"),
            "failure_type": result.get("failure_type"),
            "duration_seconds": result.get("duration_seconds"),
            "retries": result.get("retries"),
            "usage": usage if isinstance(usage, dict) else {},
            "routing_decision": (
                result.get("routing_decision") if isinstance(result.get("routing_decision"), dict) else None
            ),
            "raw_stdout_chars": len(result.get("raw_stdout") or ""),
            "raw_stderr_chars": len(result.get("raw_stderr") or ""),
            "output_chars": len(result.get("output") or ""),
        }

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
        output_dir = self.run_dir / "claude_outputs"
        debug_path = output_dir / f"plan_cycle_{cycle_num:04d}.debug.json"
        parsed = {
            "parse_status": parse_status,
            "parse_error": parse_error,
        }
        if planning_output is not None:
            parsed.update(
                {
                    "frontier_assessment": planning_output.frontier_assessment,
                    "cycle_notes": planning_output.cycle_notes,
                    "planning_complete": planning_output.planning_complete,
                    "completion_reason": planning_output.completion_reason,
                    "action_count": len(planning_output.actions),
                    "actions": [
                        {
                            "action_type": action.action_type,
                            "issue_id": action.issue_id,
                            "title": action.title,
                            "priority": action.priority,
                            "dependencies": action.dependencies,
                            "labels": action.labels,
                            "acceptance_criteria_count": len(action.acceptance_criteria or []),
                        }
                        for action in planning_output.actions
                    ],
                }
            )
        payload = {
            "cycle": cycle_num,
            "phase": self.state.phase.value if hasattr(self.state.phase, "value") else str(self.state.phase),
            "is_finalization": self.state.phase == RunPhase.PLAN_FINALIZATION,
            "prompt_chars": len(prompt_path.read_text(encoding="utf-8")),
            "prompt_path": prompt_path.name,
            "raw_output_path": output_path.name,
            "preflight_routing": preflight_routing,
            "result": self._serialize_result_metadata(result),
            "parsed": parsed,
        }
        debug_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
