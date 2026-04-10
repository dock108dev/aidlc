"""Helper functions for Planner to keep module size manageable."""

import json
import re
import time
from pathlib import Path

from .models import Issue
from .schemas import PLANNING_SCHEMA_DESCRIPTION


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
    """Build planning prompt using current planner state."""
    write_planning_index(planner)

    sections = [
        "# Planning Task\n",
        (
            "You are planning implementation work for this project. "
            "You have FULL FILE ACCESS — read any project file you need.\n\n"
            "**Start by reading these files:**\n"
            "1. **README.md** (if present) — product intent and usage expectations\n"
            "2. **ARCHITECTURE.md** — system structure\n"
            "3. **DESIGN.md** — patterns and conventions\n"
            "4. **.aidlc/planning_index.md** — index of all docs, research, and existing issues\n"
            "5. **ROADMAP.md** (optional) — milestone hints only, not authoritative\n\n"
            "Read them NOW before creating any issues."
        ),
    ]

    if planner._last_cycle_notes:
        sections.append("\n## Previous Cycle\n")
        sections.append(planner._last_cycle_notes[:500])

    if planner.state.issues:
        sections.append(f"\n## Existing Issues ({len(planner.state.issues)} total)\n")
        sections.append("One-line index. Read .aidlc/issues/{ID}.md for full specs.\n")
        for issue in planner.state.issues:
            sections.append(
                f"- {issue['id']}: {issue.get('title', '')} "
                f"[{issue.get('priority', 'medium')}]"
            )

    if planner.doc_gaps:
        critical_gaps = [g for g in planner.doc_gaps if g.severity == "critical"]
        if critical_gaps:
            sections.append("\n## Critical Doc Gaps\n")
            for gap in critical_gaps[:5]:
                sections.append(f"- `{gap.doc_path}:{gap.line}` — {gap.text[:80]}")

    sections.append("\n## Planning Foundation Status\n")
    sections.append(render_planning_foundation(planner))

    sections.append("\n## Available Files\n")
    sections.append(
        "You have full read access to the project. Use it:\n"
        "- **README.md** — product intent, constraints, and usage context\n"
        "- **ARCHITECTURE.md** — system structure\n"
        "- **DESIGN.md** — patterns and conventions\n"
        "- **ROADMAP.md** (optional) — milestone guidance if maintained\n"
        "- **.aidlc/planning_index.md** — full index of all docs, research, and issues\n"
        "- **.aidlc/issues/*.md** — full specs of existing issues\n"
        "- **docs/research/*.md** — completed research (do NOT re-request)\n\n"
        "Read specific files when you need detail. Do NOT ask for content to be "
        "pasted — just read the files directly."
    )

    plan_h = planner.state.plan_elapsed_seconds / 3600
    budget_h = planner.state.plan_budget_seconds / 3600
    sections.append("\n## Run State\n")
    sections.append(f"- Phase: {planner.state.phase.value}")
    sections.append(f"- Planning cycle: {planner.state.planning_cycles}")
    sections.append(f"- Elapsed: {plan_h:.1f}h / {budget_h:.0f}h budget")
    sections.append(f"- Issues created: {planner.state.issues_created}")
    sections.append(f"- Docs created: {planner.state.files_created}")

    if is_finalization:
        sections.append(planner._finalization_instructions())
    else:
        sections.append(planner._planning_instructions())

    sections.append(PLANNING_SCHEMA_DESCRIPTION)

    if getattr(planner, "_offer_completion", False):
        sections.append(planner._completion_offer_instructions())

    prompt = "\n\n".join(sections)
    planner.logger.info(f"  Prompt size: {len(prompt):,} chars (~{len(prompt)//4:,} tokens)")
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

    prompt = "\n".join(prompt_parts)
    research_model = planner.config.get("claude_model_research")
    start_time = time.time()
    result = planner.cli.execute_prompt(
        prompt, planner.project_root, model_override=research_model
    )
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
