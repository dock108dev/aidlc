"""Planning engine for AIDLC.

Runs time-constrained planning sessions that:
1. Scan repo docs to build project context
2. Assess what planning work needs to be done
3. Have Claude create issues with full specs and acceptance criteria
4. Loop until time budget exhausted or planning frontier is clear
"""

import re
import time
from pathlib import Path

from .models import RunState, RunPhase, Issue
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
        self._phase_docs = self._map_phase_docs()
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
                        "continuing to ensure all ROADMAP phases are covered."
                    )
                    recent_cycles.append(0)
                    # Check if we've had enough empty cycles to trigger winding down
                    if (
                        len(recent_cycles) >= diminishing_returns_threshold
                        and self.state.issues_created > 0
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
            self.logger.error(
                f"Cycle {cycle_num} failed due to {len(validation_errors)} validation error(s)"
            )
            return False

        # Only accept planning_complete if we've actually offered it
        # (Claude sometimes adds this field unprompted — ignore it until invited)
        if planning_output.planning_complete and getattr(self, "_offer_completion", False):
            reason = planning_output.completion_reason or "Claude declared planning complete"
            self._pending_completion_reason = f"Planning complete — {reason}"
            self.logger.info(f"Claude signaled planning_complete (accepted): {reason}")
        elif planning_output.planning_complete:
            self.logger.info(
                "Claude signaled planning_complete but completion not yet offered — ignoring"
            )

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

        self.logger.info(f"Cycle {cycle_num} complete: {applied} actions applied")
        return True

    def _build_prompt(self, is_finalization: bool) -> str:
        """Build the planning prompt with full project context."""
        sections = []

        # Project context from scanner
        sections.append("# Project Context\n")
        sections.append(self.project_context)

        # Phase-focused context: include full docs relevant to next uncovered phase
        next_phase = self._get_current_phase_name()
        if next_phase and self._phase_docs.get(next_phase):
            from .context_prep import build_phase_context
            phase_ctx = build_phase_context(
                self.doc_files,
                self._phase_docs[next_phase],
                max_chars=self.config.get("phase_context_max_chars", 40000),
            )
            if phase_ctx:
                sections.append(f"\n## Focus: {next_phase}\n")
                sections.append(
                    "The following detailed docs are relevant to this phase "
                    "and should inform your issue creation:\n"
                )
                sections.append(phase_ctx)
                sections.append("")

        # Documentation gaps (from doc-gap detection)
        if self.doc_gaps:
            sections.append("\n## Documentation Gaps Detected\n")
            sections.append(
                "The following gaps were found in project documentation. "
                "Use 'research' actions to investigate critical gaps before creating issues.\n"
            )
            for gap in self.doc_gaps:
                marker = "[CRITICAL] " if gap.severity == "critical" else ""
                sections.append(f"- {marker}`{gap.doc_path}:{gap.line}` — {gap.text[:120]}")
            sections.append("")

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

        # If winding down, offer Claude the option to declare planning complete
        if getattr(self, "_offer_completion", False):
            sections.append(self._completion_offer_instructions())

        return "\n\n".join(sections)

    def _planning_instructions(self) -> str:
        return """## Instructions — Planning Mode

You are an autonomous planning agent analyzing this project. Your job is to create a comprehensive
implementation plan as a set of well-specified issues.

**CRITICAL: Cover ALL phases in the ROADMAP, not just the first one.**
If the ROADMAP has Phases 1 through 4, you must create issues for ALL of them.
Do not stop after Phase 1. Each phase should have its own set of issues.
Work through the roadmap systematically — one phase per cycle if needed.

**What you should do:**
- Create issues for EVERY item in EVERY phase of the ROADMAP
- Each issue must have clear acceptance criteria that are specific and testable
- Set appropriate priority levels (high = blocking/critical, medium = important, low = nice-to-have)
- Define dependency chains — which issues must be completed before others
- Create design docs for complex features that need architectural decisions
- Use "research" actions when you encounter knowledge gaps, need to derive formulas,
  explore design alternatives, or need to analyze source code before creating issues.
  Research results are written to docs/research/ and available in subsequent cycles.

**What you should NOT do:**
- Write implementation code (that comes in the implementation phase)
- Create duplicate issues
- Create vague issues without testable acceptance criteria
- Ignore existing documentation — build on what's already planned
- Stop after covering only one phase when there are more phases in the ROADMAP

**Priority order:**
1. Core infrastructure and foundational issues (high priority, no deps)
2. Main features that depend on infrastructure
3. Secondary features and enhancements
4. Polish, optimization, and documentation

Produce 1-15 high-quality actions per cycle. Quality over quantity.
Focus each cycle on a different phase or area until all ROADMAP work is captured."""

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

    def _map_phase_docs(self) -> dict[str, list[str]]:
        """Map ROADMAP phases to relevant doc paths for phase-focused context."""
        if not self.doc_files:
            return {}
        # Find the ROADMAP content
        roadmap_content = ""
        for doc in self.doc_files:
            if doc["path"].lower() in ("roadmap.md",):
                roadmap_content = doc["content"]
                break
        if not roadmap_content:
            return {}

        from .context_prep import identify_phase_docs
        return identify_phase_docs(self.doc_files, roadmap_content)

    def _get_current_phase_name(self) -> str | None:
        """Determine which ROADMAP phase the planner should focus on next.

        Looks at which phases already have issues and suggests the next one.
        """
        if not self._phase_docs:
            return None

        phase_names = list(self._phase_docs.keys())

        # Check which phases have issues created
        issue_titles = " ".join(d.get("title", "") for d in self.state.issues).lower()

        for phase_name in phase_names:
            # If no issues mention this phase's keywords, it needs planning
            phase_lower = phase_name.lower()
            # Simple heuristic: if the phase name keywords appear in issue titles, it's covered
            phase_words = [w for w in phase_lower.split() if len(w) > 3]
            if phase_words and not any(w in issue_titles for w in phase_words):
                return phase_name

        return None

    def _completion_offer_instructions(self) -> str:
        return """## PLANNING WIND-DOWN NOTICE

The last several planning cycles have only produced minor updates to existing issues
with no new issues created. If you believe the plan is comprehensive and covers all
work described in the project documentation, you should declare planning complete.

To declare complete, add these fields to your JSON output:
  "planning_complete": true,
  "completion_reason": "Brief explanation of why the plan is complete"

You may include final refinement actions alongside the completion declaration.

If there is still meaningful work NOT captured in any issue, continue creating issues
instead of declaring complete."""

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

        elif action.action_type == "research":
            self._execute_research(action)

    def _execute_research(self, action: PlanningAction) -> None:
        """Execute a research action — call Claude to investigate a topic."""
        max_per_run = self.config.get("research_max_per_run", 10)
        if self._research_count >= max_per_run:
            self.logger.warning(
                f"Research cap reached ({max_per_run}), skipping: {action.research_topic}"
            )
            return

        self.logger.info(f"Researching: {action.research_topic}")

        # Read scope files
        max_files = self.config.get("research_max_scope_files", 10)
        max_chars = self.config.get("research_max_source_chars", 15000)
        scope_content = []
        for scope_path in (action.research_scope or [])[:max_files]:
            full_path = self.project_root / scope_path
            if full_path.exists() and full_path.is_file():
                try:
                    content = full_path.read_text(errors="replace")
                    if len(content) > max_chars:
                        content = content[:max_chars] + "\n\n... (truncated)"
                    scope_content.append(f"### {scope_path}\n```\n{content}\n```")
                except OSError:
                    self.logger.warning(f"Could not read scope file: {scope_path}")
            else:
                self.logger.warning(f"Scope file not found: {scope_path}")

        # Build research prompt
        prompt_parts = [
            f"# Research: {action.research_topic}",
            "",
            "## Question",
            action.research_question or action.rationale,
            "",
        ]
        if scope_content:
            prompt_parts.append("## Relevant Source Files\n")
            prompt_parts.extend(scope_content)
            prompt_parts.append("")

        prompt_parts.extend([
            "## Instructions",
            "",
            "Analyze the question and any source files above. Write a thorough research",
            "document in markdown that:",
            "- Answers the research question with specific, actionable recommendations",
            "- References relevant code sections if scope files were provided",
            "- Identifies trade-offs between alternatives",
            "- Provides concrete implementation guidance",
            "- Includes formulas, algorithms, or design patterns as applicable",
            "",
            "Output your response as a markdown document. No JSON wrapping needed.",
        ])

        prompt = "\n".join(prompt_parts)

        # Call Claude
        start_time = time.time()
        result = self.cli.execute_prompt(prompt, self.project_root)
        duration = time.time() - start_time
        self.state.plan_elapsed_seconds += duration
        self.state.elapsed_seconds += duration

        if not result["success"]:
            self.logger.error(f"Research failed for {action.research_topic}: {result.get('error')}")
            return

        output = result.get("output", "")
        if not output:
            self.logger.warning(f"Research returned empty output for {action.research_topic}")
            return

        # Write research output
        sanitized = re.sub(r"[^a-z0-9_-]", "-", action.research_topic.lower())
        sanitized = re.sub(r"-+", "-", sanitized).strip("-")[:80]
        research_dir = self.project_root / "docs" / "research"
        research_dir.mkdir(parents=True, exist_ok=True)
        output_path = research_dir / f"{sanitized}.md"

        # Add header
        full_content = (
            f"# Research: {action.research_topic}\n\n"
            f"*Auto-generated by AIDLC research phase*\n\n"
            f"**Question:** {action.research_question}\n\n"
            f"---\n\n"
            f"{output}"
        )
        output_path.write_text(full_content)

        self.state.files_created += 1
        self.state.created_artifacts.append({
            "path": f"docs/research/{sanitized}.md",
            "type": "research",
            "action": "create",
        })
        self._research_count += 1
        self.logger.info(f"Research complete: docs/research/{sanitized}.md")

        # Save raw output
        output_dir = self.run_dir / "claude_outputs"
        output_dir.mkdir(exist_ok=True)
        (output_dir / f"research_{sanitized}.md").write_text(output)

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
