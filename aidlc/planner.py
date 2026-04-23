"""Planning engine for AIDLC.

Runs time-constrained planning sessions that:
1. Scan repo docs to build project context
2. Assess what planning work needs to be done
3. Have the routed AI provider create issues with full specs and acceptance criteria
4. Loop until time budget exhausted or planning frontier is clear
"""

import time
from pathlib import Path

from .logger import log_checkpoint
from .models import Issue, RunPhase, RunState
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
        # ISSUE-011: legacy fixed threshold is still respected when set, but the
        # primary control is now the adaptive min/max. The effective threshold
        # is recomputed each cycle from the current issue count.
        legacy_threshold = self.config.get("diminishing_returns_threshold")
        if legacy_threshold is not None:
            self.logger.info(
                "config: diminishing_returns_threshold is deprecated; use "
                "planning_diminishing_returns_min_threshold / _max_threshold instead"
            )
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
                    # Check if we've had enough empty cycles to trigger winding down.
                    # ISSUE-011: threshold scales with issue count.
                    threshold = self._adaptive_diminishing_threshold(legacy_threshold)
                    if (
                        len(recent_cycles) >= threshold
                        and len(self.state.issues) > 0
                        and self._planning_foundation.get("ready", False)
                        and all(n == 0 for n in recent_cycles[-threshold:])
                    ):
                        if not self._offer_completion:
                            self._offer_completion = True
                            self.logger.info(
                                f"Offering completion option after {threshold} empty cycles "
                                f"(adaptive threshold for {len(self.state.issues)} issues)."
                            )
                        else:
                            self.state.stop_reason = "Planning frontier is clear"
                            self.logger.info(
                                f"Planning complete after {threshold} empty cycles "
                                f"(adaptive threshold)."
                            )
                            break
            elif result is False:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    self.state.stop_reason = (
                        f"{max_consecutive_failures} consecutive planning failures"
                    )
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
                # ISSUE-011: threshold scales with issue count, recomputed each cycle
                threshold = self._adaptive_diminishing_threshold(legacy_threshold)
                if (
                    len(recent_cycles) >= threshold
                    and len(self.state.issues) > 0
                    and self._planning_foundation.get("ready", False)
                    and all(n == 0 for n in recent_cycles[-threshold:])
                ):
                    if not self._offer_completion:
                        # First detection: tell Claude it can declare done next cycle
                        self._offer_completion = True
                        self.logger.info(
                            f"Winding down detected: {threshold} cycles "
                            f"with no new issues (adaptive threshold for "
                            f"{len(self.state.issues)} issues). Offering completion to Claude."
                        )
                    elif self._pending_completion_reason:
                        # Claude accepted the offer — honor it
                        self.state.stop_reason = self._pending_completion_reason
                        self.logger.info(
                            f"Planning complete (confirmed): {self._pending_completion_reason}"
                        )
                        break
                    else:
                        # Claude didn't declare complete but is still just updating
                        # Give it one more cycle, then force exit
                        tail_len = sum(1 for n in recent_cycles if n == 0)
                        if tail_len >= threshold + 2:
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
        # ISSUE-016, both created in the same cycle).
        # Include existing issues from previous runs so dependencies to them are valid.
        # existing_issues are {"path": "...", "content": "..."} dicts — extract the
        # issue ID from the filename stem (e.g. ".aidlc/issues/ISSUE-020.md" → "ISSUE-020").
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

        # Only accept planning_complete if we've offered it and planning docs are sufficient.
        # (Claude sometimes adds this field unprompted — ignore it until invited)
        foundation_ready = self._planning_foundation.get("ready", False)
        if (
            planning_output.planning_complete
            and getattr(self, "_offer_completion", False)
            and foundation_ready
        ):
            reason = planning_output.completion_reason or "planning completed"
            self._pending_completion_reason = f"Planning complete — {reason}"
            self.logger.info(f"Model signaled planning_complete (accepted): {reason}")
        elif planning_output.planning_complete:
            if not foundation_ready:
                self.logger.warning(
                    "Model signaled planning_complete but planning docs are still incomplete "
                    "(missing/thin foundation docs) — ignoring"
                )
            else:
                self.logger.info(
                    "Model signaled planning_complete but completion not yet offered — ignoring"
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

    def _adaptive_diminishing_threshold(self, legacy_threshold: int | None) -> int:
        """Compute the diminishing-returns threshold for the current issue count.

        ISSUE-011: scale the threshold with project size:
            threshold = clamp(min, ceil(num_issues_so_far / 10), max)

        - Small projects (≤30 issues) use the floor (default 3).
        - Large projects (≥60 issues) use the ceiling (default 6).
        - In between, the threshold steps up by one per ~10 issues so a stall
          mid-planning on a big repo doesn't force-exit prematurely.

        When the deprecated ``diminishing_returns_threshold`` is set, it is
        used as the floor (so existing user customizations don't regress).
        """
        from math import ceil

        floor_default = 3 if legacy_threshold is None else int(legacy_threshold)
        floor_val = max(
            1,
            int(self.config.get("planning_diminishing_returns_min_threshold", floor_default)),
        )
        ceil_val = max(
            floor_val,
            int(self.config.get("planning_diminishing_returns_max_threshold", 6)),
        )
        n = len(self.state.issues or [])
        adaptive = ceil(max(1, n) / 10)
        return max(floor_val, min(adaptive, ceil_val))

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

        elif action.action_type in ("create_doc", "update_doc"):
            file_path = self.project_root / action.file_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(action.content)
            self._upsert_doc_file(action.file_path, action.content)
            self.state.files_created += 1
            self.state.created_artifacts.append(
                {
                    "path": action.file_path,
                    "type": "doc",
                    "action": "create" if action.action_type == "create_doc" else "update",
                }
            )
            self.logger.info(
                f"{'Created' if action.action_type == 'create_doc' else 'Updated'} doc: {action.file_path}"
            )

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

    def _log_provider_result(self, cycle_num: int, result: dict) -> None:
        """Log which provider/model handled the planning cycle."""
        provider = str(result.get("provider_id") or "unknown")
        model = str(result.get("model_used") or "unknown")
        self.logger.info(f"  Planning Cycle {cycle_num} model: {provider}/{model}")

    def _sanitize_issue_dependencies(self) -> int:
        """Normalize dependency graph to avoid implementation stalls.

        Rules:
        - Remove self-dependencies.
        - Remove dependencies pointing to missing issues.
        - De-duplicate dependency lists while preserving order.
        - Auto-break cycles by removing one circular edge per detected cycle.
        """
        if not self.state.issues:
            return 0

        priority_order = {"high": 0, "medium": 1, "low": 2}
        id_to_issue = {d["id"]: d for d in self.state.issues if d.get("id")}
        issue_ids = set(id_to_issue.keys())
        touched: set[str] = set()
        total_changes = 0

        for issue_id, issue_data in id_to_issue.items():
            deps = issue_data.get("dependencies") or []
            if not isinstance(deps, list):
                deps = []
            cleaned: list[str] = []
            seen: set[str] = set()
            for dep in deps:
                if not isinstance(dep, str):
                    total_changes += 1
                    touched.add(issue_id)
                    self.logger.warning(f"Dropped non-string dependency on {issue_id}: {dep!r}")
                    continue
                dep_norm = dep.strip().upper()
                if not dep_norm:
                    total_changes += 1
                    touched.add(issue_id)
                    self.logger.warning(f"Dropped empty dependency on {issue_id}")
                    continue
                if dep_norm == issue_id:
                    total_changes += 1
                    touched.add(issue_id)
                    self.logger.warning(f"Removed self-dependency: {issue_id} -> {dep_norm}")
                    continue
                if dep_norm not in issue_ids:
                    total_changes += 1
                    touched.add(issue_id)
                    self.logger.warning(
                        f"Removed unknown dependency: {issue_id} -> {dep_norm} (target missing)"
                    )
                    continue
                if dep_norm in seen:
                    total_changes += 1
                    touched.add(issue_id)
                    self.logger.warning(f"Removed duplicate dependency: {issue_id} -> {dep_norm}")
                    continue
                seen.add(dep_norm)
                cleaned.append(dep_norm)
            issue_data["dependencies"] = cleaned

        def detect_cycles() -> list[list[str]]:
            cycles: list[list[str]] = []
            cycle_keys: set[tuple[str, ...]] = set()
            visited: set[str] = set()
            temp: set[str] = set()

            def visit(issue_id: str, path: list[str]) -> None:
                if issue_id in visited:
                    return
                if issue_id in temp:
                    start = path.index(issue_id)
                    cycle = path[start:] + [issue_id]
                    key = tuple(sorted(cycle[:-1]))
                    if key and key not in cycle_keys:
                        cycle_keys.add(key)
                        cycles.append(cycle)
                    return
                temp.add(issue_id)
                for dep in id_to_issue.get(issue_id, {}).get("dependencies", []):
                    if dep in id_to_issue:
                        visit(dep, path + [issue_id])
                temp.discard(issue_id)
                visited.add(issue_id)

            for issue_id in sorted(id_to_issue.keys()):
                visit(issue_id, [])
            return cycles

        max_passes = max(1, len(id_to_issue) * 2)
        for _ in range(max_passes):
            cycles = detect_cycles()
            if not cycles:
                break

            for cycle in cycles:
                core = cycle[:-1]
                if not core:
                    continue
                cycle_str = " -> ".join(cycle)
                self.logger.warning(f"Circular dependency detected during planning: {cycle_str}")

                candidate = max(
                    core,
                    key=lambda iid: (
                        priority_order.get(id_to_issue.get(iid, {}).get("priority", "medium"), 1),
                        len(id_to_issue.get(iid, {}).get("dependencies", [])),
                        iid,
                    ),
                )
                idx = core.index(candidate)
                successor = cycle[idx + 1]
                deps = list(id_to_issue.get(candidate, {}).get("dependencies", []))
                removed = False
                if successor in deps:
                    deps.remove(successor)
                    removed = True
                else:
                    for dep in deps:
                        if dep in core:
                            successor = dep
                            deps.remove(dep)
                            removed = True
                            break
                if removed:
                    id_to_issue[candidate]["dependencies"] = deps
                    touched.add(candidate)
                    total_changes += 1
                    self.logger.warning(
                        f"Removed circular dependency edge during planning: {candidate} -> {successor}"
                    )
        else:
            self.logger.error(
                "Dependency sanitization exceeded max passes; unresolved cycles may remain."
            )

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
