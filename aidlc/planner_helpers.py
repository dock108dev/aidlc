"""Helper functions for Planner to keep module size manageable."""

import json
import re
import time
from pathlib import Path

from .models import Issue
from .research_output import (
    add_research_output_constraints,
    build_repair_prompt,
    is_permission_chatter,
)
from .schemas import PLANNING_SCHEMA_DESCRIPTION


def _issue_number(issue_id: str) -> int:
    """Extract numeric suffix from ISSUE-123 style IDs."""
    match = re.search(r"(\d+)$", issue_id or "")
    if not match:
        return -1
    return int(match.group(1))


def _render_existing_issues_section(planner) -> list[str]:
    """Render a bounded issue section so planning prompt size stays stable."""
    issues = list(planner.state.issues or [])
    if not issues:
        return []

    total = len(issues)
    max_items = max(5, int(planner.config.get("planning_issue_index_max_items", 40)))
    include_all_until = max(
        0, int(planner.config.get("planning_issue_index_include_all_until", 30))
    )

    lines = [f"\n## Existing Issues ({total} total)\n"]

    if total <= include_all_until:
        lines.append("One-line index. Read .aidlc/issues/{ID}.md for full specs.\n")
        selected = issues
    else:
        status_counts = {}
        priority_counts = {"high": 0, "medium": 0, "low": 0}
        for issue in issues:
            status = issue.get("status", "pending")
            status_counts[status] = status_counts.get(status, 0) + 1
            prio = issue.get("priority", "medium")
            if prio in priority_counts:
                priority_counts[prio] += 1

        lines.append(
            "Compact issue index (bounded for token control). "
            "Read .aidlc/issues/{ID}.md for full specs.\n"
        )
        lines.append(
            f"- Priority totals: high={priority_counts['high']}, "
            f"medium={priority_counts['medium']}, low={priority_counts['low']}"
        )
        status_summary = ", ".join(
            f"{status}={count}" for status, count in sorted(status_counts.items())
        )
        lines.append(f"- Status totals: {status_summary}")

        high_priority_pending = [
            issue
            for issue in issues
            if issue.get("priority") == "high" and issue.get("status", "pending") in (
                "pending",
                "in_progress",
                "blocked",
                "failed",
            )
        ]
        high_priority_pending.sort(
            key=lambda issue: _issue_number(issue.get("id", "")),
            reverse=True,
        )
        recent_issues = sorted(
            issues,
            key=lambda issue: _issue_number(issue.get("id", "")),
            reverse=True,
        )

        selected = []
        selected_ids = set()
        half_budget = max_items // 2

        for issue in high_priority_pending:
            issue_id = issue.get("id")
            if not issue_id or issue_id in selected_ids:
                continue
            selected.append(issue)
            selected_ids.add(issue_id)
            if len(selected) >= half_budget:
                break

        for issue in recent_issues:
            if len(selected) >= max_items:
                break
            issue_id = issue.get("id")
            if not issue_id or issue_id in selected_ids:
                continue
            selected.append(issue)
            selected_ids.add(issue_id)

        omitted = max(0, total - len(selected))
        if omitted:
            lines.append(
                f"- Omitted from inline list: {omitted} older/lower-priority issues "
                "(still available in .aidlc/issues/)"
            )

    # Stable order → fewer cache-breaking diffs when the same set is selected.
    selected = sorted(selected, key=lambda x: _issue_number(x.get("id", "")))

    for issue in selected:
        title = (issue.get("title", "") or "").strip()
        if len(title) > 90:
            title = f"{title[:87]}..."
        deps = issue.get("dependencies") or []
        dep_s = ",".join(deps) if deps else "-"
        lines.append(
            f"- {issue.get('id', 'UNKNOWN')}: {title} "
            f"[{issue.get('priority', 'medium')}/{issue.get('status', 'pending')}] "
            f"deps:{dep_s}"
        )
    return lines


def _enforce_prompt_budget(prompt: str, planner) -> str:
    """Shrink planning prompt to configured budget while preserving key instructions."""
    max_chars = max(4000, int(planner.config.get("max_planning_prompt_chars", 60000) or 60000))
    if len(prompt) <= max_chars:
        return prompt

    planner.logger.warning(
        f"Planning prompt exceeded budget ({len(prompt):,} > {max_chars:,}); shrinking context"
    )

    shrunk = re.sub(
        r"\n## Existing Issues[\s\S]*?(?=\n## |\Z)",
        "\n## Existing Issues\nUse .aidlc/planning_index.md and .aidlc/issues/*.md for full backlog details.",
        prompt,
        count=1,
    )
    if len(shrunk) <= max_chars:
        return shrunk

    shrunk = re.sub(
        r"\n## Previous Cycle[\s\S]*?(?=\n## |\Z)",
        "\n## Previous Cycle\nSee prior cycle notes in this run's planning outputs.",
        shrunk,
        count=1,
    )
    if len(shrunk) <= max_chars:
        return shrunk

    marker = "\n\n[planning prompt truncated to fit max_planning_prompt_chars]\n"
    keep = max(1000, max_chars - len(marker))
    return shrunk[:keep] + marker


def write_planning_index(planner) -> Path:
    """Write an index file that Claude can reference instead of pasting everything."""
    index_path = planner.run_dir.parent.parent / "planning_index.md"
    lines = ["# AIDLC Planning Index", ""]

    lines.append("## Key Project Docs (read these for full detail)")
    for name in [
        "README.md",
        "ARCHITECTURE.md",
        "DESIGN.md",
        "CLAUDE.md",
        "STATUS.md",
        "ROADMAP.md",
    ]:
        if (planner.project_root / name).exists():
            lines.append(f"- {name}")
    lines.append("")

    research_dir = planner.project_root / "docs" / "research"
    if research_dir.exists():
        research_files = sorted(research_dir.glob("*.md"))
        if research_files:
            lines.append("## Completed Research (do NOT re-request)")
            for rf in research_files:
                lines.append(f"- docs/research/{rf.name}")
            lines.append("")

    issues_dir = Path(planner.config["_issues_dir"])
    if issues_dir.exists():
        issue_files = sorted(issues_dir.glob("*.md"))
        if issue_files:
            lines.append(f"## Existing Issues ({len(issue_files)} files in .aidlc/issues/)")
            lines.append("Read individual issue files for full specs:")
            for issue_file in issue_files:
                lines.append(f"- .aidlc/issues/{issue_file.name}")
            lines.append("")

    if planner.state.issues:
        issues = sorted(
            planner.state.issues,
            key=lambda issue: _issue_number(issue.get("id", "")),
        )
        lines.append("## Issue Backlog Summary")
        lines.append(f"- Total issues: {len(issues)}")

        status_counts = {}
        priority_counts = {"high": 0, "medium": 0, "low": 0}
        label_counts = {}
        for issue in issues:
            status = issue.get("status", "pending")
            status_counts[status] = status_counts.get(status, 0) + 1
            priority = issue.get("priority", "medium")
            if priority in priority_counts:
                priority_counts[priority] += 1
            for label in issue.get("labels", []) or []:
                label_counts[label] = label_counts.get(label, 0) + 1

        completed = sum(
            status_counts.get(name, 0) for name in ("implemented", "verified", "skipped")
        )
        completion_pct = (completed / len(issues)) * 100 if issues else 0.0
        lines.append(f"- Completion: {completed}/{len(issues)} ({completion_pct:.1f}%)")
        lines.append(
            f"- Priority totals: high={priority_counts['high']}, "
            f"medium={priority_counts['medium']}, low={priority_counts['low']}"
        )
        status_totals = ", ".join(
            f"{status}={count}" for status, count in sorted(status_counts.items())
        )
        lines.append(f"- Status totals: {status_totals}")
        lines.append("")

        if label_counts:
            lines.append("### Category Rollup (Labels)")
            for label, count in sorted(label_counts.items(), key=lambda item: (-item[1], item[0])):
                lines.append(f"- {label}: {count}")
            lines.append("")

        lines.append("### Active Issues")
        active_statuses = ("pending", "in_progress", "blocked", "failed")
        active_issues = [issue for issue in issues if issue.get("status", "pending") in active_statuses]
        if active_issues:
            for issue in active_issues:
                labels = ", ".join(issue.get("labels", []) or [])
                label_part = f" labels: {labels}" if labels else ""
                lines.append(
                    f"- {issue.get('id', 'UNKNOWN')} [{issue.get('status', 'pending')}] "
                    f"[{issue.get('priority', 'medium')}] — {issue.get('title', '')}{label_part}"
                )
        else:
            lines.append("- none")
        lines.append("")

        lines.append("### Completed Issues")
        done_statuses = ("implemented", "verified", "skipped")
        done_issues = [issue for issue in issues if issue.get("status", "pending") in done_statuses]
        if done_issues:
            for issue in done_issues:
                lines.append(
                    f"- {issue.get('id', 'UNKNOWN')} [{issue.get('status', 'pending')}] "
                    f"[{issue.get('priority', 'medium')}] — {issue.get('title', '')}"
                )
        else:
            lines.append("- none")
        lines.append("")

    if planner.doc_files:
        other_docs = [
            d["path"]
            for d in planner.doc_files
            if d["path"].lower()
            not in (
                "architecture.md",
                "design.md",
                "claude.md",
                "status.md",
                "readme.md",
                "roadmap.md",
            )
        ]
        if other_docs:
            lines.append("## Other Project Docs")
            for path in other_docs[:30]:
                lines.append(f"- {path}")
            if len(other_docs) > 30:
                lines.append(f"- ... and {len(other_docs) - 30} more")
            lines.append("")

    index_path.write_text("\n".join(lines))
    return index_path


def build_prompt(planner, is_finalization: bool) -> str:
    """Build planning prompt: static instructions + schema first (cache-friendly), volatile last."""
    write_planning_index(planner)

    # Static prefix — identical across cycles when phase and completion-offer flag unchanged.
    static_parts: list[str] = []
    if is_finalization:
        static_parts.append(planner._finalization_instructions())
    else:
        static_parts.append(planner._planning_instructions())
    if getattr(planner, "_offer_completion", False):
        static_parts.append(planner._completion_offer_instructions())
    static_parts.append(PLANNING_SCHEMA_DESCRIPTION)

    volatile_parts: list[str] = [
        "# Planning Task\n",
        (
            "Read: `README.md`, `ARCHITECTURE.md`, `DESIGN.md`, `.aidlc/planning_index.md`, "
            "`.aidlc/issues/*.md`; `ROADMAP.md` optional. Full file access — do not ask for pastes."
        ),
    ]

    if planner._last_cycle_notes:
        max_prev_notes = max(
            100, int(planner.config.get("planning_last_cycle_notes_max_chars", 500))
        )
        volatile_parts.append("\n## Previous Cycle\n")
        volatile_parts.append(planner._last_cycle_notes[:max_prev_notes])

    volatile_parts.extend(_render_existing_issues_section(planner))

    if planner.doc_gaps:
        critical_gaps = [g for g in planner.doc_gaps if g.severity == "critical"]
        if critical_gaps:
            volatile_parts.append("\n## Critical Doc Gaps\n")
            for gap in critical_gaps[:5]:
                volatile_parts.append(f"- `{gap.doc_path}:{gap.line}` — {gap.text[:80]}")
        non_crit = [g for g in planner.doc_gaps if g.severity != "critical"]
        if non_crit:
            volatile_parts.append(
                f"\n## Doc gaps (non-critical): {len(non_crit)} — scan repo / planning_index for details\n"
            )

    volatile_parts.append("\n## Planning Foundation Status\n")
    volatile_parts.append(render_planning_foundation(planner))

    plan_h = planner.state.plan_elapsed_seconds / 3600
    budget_h = planner.state.plan_budget_seconds / 3600
    volatile_parts.append("\n## Run State\n")
    volatile_parts.append(f"- phase: {planner.state.phase.value}")
    volatile_parts.append(f"- planning_cycle: {planner.state.planning_cycles}")
    volatile_parts.append(f"- elapsed/budget: {plan_h:.1f}h / {budget_h:.0f}h")
    volatile_parts.append(f"- issues_created: {planner.state.issues_created}")
    volatile_parts.append(f"- docs_created: {planner.state.files_created}")

    prompt = "\n\n".join(static_parts + volatile_parts)
    prompt = _enforce_prompt_budget(prompt, planner)
    planner.logger.info(f"  Prompt size: {len(prompt):,} chars (~{len(prompt) // 4:,} tokens)")
    return prompt


def execute_research(planner, action) -> None:
    """Execute a research action for planner."""
    max_per_cycle = planner.config.get("research_max_per_cycle", 2)
    if planner._cycle_research_count >= max_per_cycle:
        planner.logger.info(
            f"Research cycle cap reached ({max_per_cycle}), deferring: {action.research_topic} "
            "(will be available next cycle)"
        )
        return

    sanitized = re.sub(r"[^a-z0-9_-]", "-", action.research_topic.lower())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")[:80]
    output_path = planner.project_root / "docs" / "research" / f"{sanitized}.md"
    if output_path.exists():
        planner.logger.info(
            f"Research already exists: docs/research/{sanitized}.md — skipping"
        )
        return

    planner.logger.info(f"Researching: {action.research_topic}")
    max_files = planner.config.get("research_max_scope_files", 10)
    max_chars = planner.config.get("research_max_source_chars", 15000)
    scope_content = []
    for scope_path in (action.research_scope or [])[:max_files]:
        full_path = planner.project_root / scope_path
        if full_path.exists() and full_path.is_file():
            try:
                content = full_path.read_text(errors="replace")
                if len(content) > max_chars:
                    content = content[:max_chars] + "\n\n... (truncated)"
                scope_content.append(f"### {scope_path}\n```\n{content}\n```")
            except OSError:
                planner.logger.warning(f"Could not read scope file: {scope_path}")
        else:
            planner.logger.warning(f"Scope file not found: {scope_path}")

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

    prompt_parts.extend(
        [
            "## Instructions",
            "",
            "Write a thorough, CONCRETE research document. This document will be used",
            "directly by an implementation agent, so it must contain specific, usable content.",
            "",
            "If this is content design (items, levels, characters, cards, etc.):",
            "- Create the ACTUAL content, not just guidelines",
            "- List every item/level/card with specific names, stats, descriptions, and properties",
            "- Include data that could be directly converted into JSON/config files",
            "- Be creative and thorough — design ALL the content, not a sample",
            "",
            "If this is system design (mechanics, formulas, algorithms):",
            "- Provide actual formulas with variables defined",
            "- Include worked examples with real numbers",
            "- Define edge cases and boundary conditions",
            "- Specify data structures and state transitions",
            "",
            "If this is creative design (names, themes, flavor text):",
            "- Generate ALL the names/themes/text needed, not just examples",
            "- Be specific and consistent with the project's tone",
            "",
            "IMPORTANT — Copyright and originality:",
            "- All content MUST be original. Never use real brand names, product names,",
            "  character names, or copyrighted material.",
            "- If the project parodies or spoofs real-world things, create ORIGINAL",
            "  parody names and content that are clearly transformative.",
            "- Fictional brands, characters, and products must be your own creations.",
            "",
            "The document should contain:",
            "- Answers the research question with specific, actionable content",
            "- References relevant code sections if scope files were provided",
            "- Identifies trade-offs between alternatives",
            "- Provides concrete implementation guidance",
            "- Includes formulas, algorithms, or design patterns as applicable",
            "",
            "Output your response as a markdown document. No JSON wrapping needed.",
        ]
    )

    prompt = add_research_output_constraints("\n".join(prompt_parts))
    start_time = time.time()
    result = planner.cli.execute_prompt(
        prompt, planner.project_root
    )
    planner.state.record_provider_result(result, planner.config, phase="research")
    duration = time.time() - start_time
    planner.state.plan_elapsed_seconds += duration
    planner.state.elapsed_seconds += duration

    if not result["success"]:
        planner.logger.error(
            f"Research failed for {action.research_topic}: {result.get('error')}"
        )
        return

    output = result.get("output", "")
    if not output:
        planner.logger.warning(f"Research returned empty output for {action.research_topic}")
        return
    if is_permission_chatter(output):
        planner.logger.warning(
            "Research output requested write permissions; retrying with stricter constraints"
        )
        retry_prompt = build_repair_prompt(
            action.research_topic,
            action.research_question or action.rationale,
            output,
        )
        retry_start = time.time()
        retry_result = planner.cli.execute_prompt(
            retry_prompt,
            planner.project_root,
        )
        planner.state.record_provider_result(retry_result, planner.config, phase="research")
        retry_duration = time.time() - retry_start
        planner.state.plan_elapsed_seconds += retry_duration
        planner.state.elapsed_seconds += retry_duration
        if not retry_result["success"] or not retry_result.get("output"):
            planner.logger.error(
                f"Research retry failed for {action.research_topic}: {retry_result.get('error')}"
            )
            return
        output = retry_result["output"]
        if is_permission_chatter(output):
            planner.logger.error(
                f"Research output for {action.research_topic} still contains permission chatter; skipping write"
            )
            return

    research_dir = planner.project_root / "docs" / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    full_content = (
        f"# Research: {action.research_topic}\n\n"
        "*Auto-generated by AIDLC research phase*\n\n"
        f"**Question:** {action.research_question}\n\n"
        "---\n\n"
        f"{output}"
    )
    output_path.write_text(full_content)

    planner.state.files_created += 1
    planner.state.created_artifacts.append(
        {
            "path": f"docs/research/{sanitized}.md",
            "type": "research",
            "action": "create",
        }
    )
    planner._research_count += 1
    planner._cycle_research_count += 1
    planner.logger.info(f"Research complete: docs/research/{sanitized}.md")

    output_dir = planner.run_dir / "claude_outputs"
    output_dir.mkdir(exist_ok=True)
    (output_dir / f"research_{sanitized}.md").write_text(output)


def assess_planning_foundation(planner) -> dict:
    """Assess whether core planning docs are present and sufficiently detailed."""
    required_docs = ("ARCHITECTURE.md", "DESIGN.md", "CLAUDE.md")
    by_lower = {d.get("path", "").lower(): d for d in planner.doc_files}
    details = []
    missing = []
    thin = []
    min_chars = planner.planning_doc_min_chars

    for name in required_docs:
        doc = by_lower.get(name.lower())
        if not doc:
            details.append(f"- {name}: missing")
            missing.append(name)
            continue

        content = (doc.get("content") or "").strip()
        char_count = len(content)
        placeholder_hits = sum(
            token in content.lower()
            for token in ("tbd", "todo", "to be determined", "[tbd]", "[todo]")
        )
        if char_count < min_chars or placeholder_hits >= 3:
            details.append(
                f"- {name}: thin ({char_count} chars, placeholder markers: {placeholder_hits})"
            )
            thin.append(name)
        else:
            details.append(f"- {name}: ok ({char_count} chars)")

    ready = not missing and not thin
    return {
        "ready": ready,
        "missing": missing,
        "thin": thin,
        "details": details,
    }


def render_planning_foundation(planner) -> str:
    """Render planning foundation status as markdown snippet."""
    foundation = planner._planning_foundation
    lines = [
        f"- Foundation ready: {'yes' if foundation.get('ready') else 'no'}",
        *foundation.get("details", []),
    ]
    if not foundation.get("ready"):
        lines.append(
            "- Required action: prioritize create_doc/update_doc actions before declaring completion."
        )
    return "\n".join(lines)


def render_issue_md(issue: Issue) -> str:
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
    for criterion in issue.acceptance_criteria:
        lines.append(f"- [ ] {criterion}")

    if issue.implementation_notes:
        lines.extend(["", "## Implementation Notes", "", issue.implementation_notes])
    return "\n".join(lines)


def load_last_cycle_notes(run_dir: Path) -> str:
    """Load summary notes from prior planning cycle if present."""
    notes_path = run_dir / "planning_context.json"
    if notes_path.exists():
        try:
            data = json.loads(notes_path.read_text())
            return data.get("last_cycle_summary", "")
        except (OSError, json.JSONDecodeError):
            return ""
    return ""


def save_cycle_notes(run_dir: Path, frontier: str, notes: str, cycle_num: int) -> None:
    """Persist cycle summary notes for resume continuity."""
    notes_path = run_dir / "planning_context.json"
    data = {
        "last_cycle": cycle_num,
        "last_cycle_summary": (
            f"Last planning cycle ({cycle_num}) assessment: {frontier}\n"
            f"Notes: {notes}"
        ),
    }
    notes_path.write_text(json.dumps(data, indent=2))


def upsert_doc_file(planner, rel_path: str, content: str) -> None:
    """Update planner doc cache and recompute planning foundation."""
    rel_path_norm = rel_path.replace("\\", "/")
    size = len(content)
    priority = 1
    for idx, doc in enumerate(planner.doc_files):
        if doc.get("path", "").lower() == rel_path_norm.lower():
            planner.doc_files[idx] = {
                "path": rel_path_norm,
                "content": content,
                "priority": doc.get("priority", priority),
                "size": size,
            }
            planner._planning_foundation = planner._assess_planning_foundation()
            return

    planner.doc_files.append(
        {
            "path": rel_path_norm,
            "content": content,
            "priority": priority,
            "size": size,
        }
    )
    planner._planning_foundation = planner._assess_planning_foundation()
