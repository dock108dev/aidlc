"""Helper functions for Planner to keep module size manageable."""

import json
import re
from pathlib import Path

from .models import Issue
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
            if issue.get("priority") == "high"
            and issue.get("status", "pending")
            in (
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


def _render_prior_run_issues_section(planner) -> list[str]:
    """Render prior-run issues (loaded from disk) under "do not redo" framing.

    ISSUE-005: tells the planner that prior verified/implemented issues
    represent committed work and should not be re-created. Without this, a
    re-run on an already-aidlc'd repo regenerates the plan from scratch
    (often rewriting working systems). Drops first under prompt-budget
    pressure so it cannot starve the schema/instructions section.
    """
    prior = list(getattr(planner, "existing_issues", None) or [])
    if not prior:
        return []

    # De-duplicate against the current run's issues so we don't render the
    # same ID twice (the planner's ID-collision-avoidance keeps ID space
    # consistent across runs but the same issue can appear in both lists
    # right after a resume).
    current_ids = {d.get("id") for d in (planner.state.issues or [])}

    rendered: list[tuple[str, str, str, str]] = []
    for entry in prior:
        parsed = entry.get("parsed_issue") or {}
        if not isinstance(parsed, dict):
            continue
        issue_id = parsed.get("id")
        if not issue_id or issue_id in current_ids:
            continue
        title = (parsed.get("title") or "").strip()
        if len(title) > 90:
            title = f"{title[:87]}..."
        status = parsed.get("status", "pending")
        notes = (parsed.get("implementation_notes") or "").strip()
        # Reduce notes to a single line for prompt density.
        first_line = notes.splitlines()[0].strip() if notes else ""
        if len(first_line) > 80:
            first_line = f"{first_line[:77]}..."
        rendered.append((issue_id, title, status, first_line))

    if not rendered:
        return []

    rendered.sort(key=lambda row: _issue_number(row[0]))

    lines = [
        f"\n## Prior Run — Already Done (do not redo) [{len(rendered)} issues]\n",
        "These issues exist on disk from prior aidlc runs. Verified or implemented "
        "items are committed work. Do NOT recreate. Focus your planning on deltas: "
        "real gaps in coverage, regressions, or follow-on work documented in their notes.\n",
    ]
    for issue_id, title, status, note_line in rendered:
        suffix = f" — {note_line}" if note_line else ""
        lines.append(f"- {issue_id} [{status}]: {title}{suffix}")
    return lines


def _render_foundation_docs_section(planner) -> list[str]:
    """Render the root-level BRAINDUMP.md in full as the cycle's intent source.

    This is the only doc the planner injects into the prompt. The repo itself
    is authoritative for "what is" (the model has file access — it can read
    anything it needs, and `--audit` produces a pre-flight summary). BRAINDUMP
    is authoritative for "what next". Roadmaps, architecture docs, design
    docs, audits, ADRs, etc. are not scope — they're reference material the
    model can pull on demand.

    Only the **root** BRAINDUMP.md counts. Files like `docs/audits/braindump.md`
    are historical snapshots; matching them as foundation docs silently swapped
    scope to month-old assessments and produced backlogs full of cut-list items.
    """
    docs = list(getattr(planner, "doc_files", None) or [])
    if not docs:
        return []

    braindump = None
    for doc in docs:
        path = (doc.get("path") or "").replace("\\", "/")
        if path.lower() == "braindump.md":
            braindump = doc
            break

    if braindump is None:
        return []

    content = (braindump.get("content") or "").strip()
    if not content:
        return []

    return [
        "\n## BRAINDUMP — Intent Source (authoritative)\n",
        "BRAINDUMP.md is the **intent source** — the owner's black-box description "
        "of what should be true after this cycle. It is not an implementation "
        "spec. Translate it into issues by consulting the discovery findings "
        "and research notes below: file the real work needed to deliver the "
        "intent, including prereq/infra/refactor/test issues and per-concern "
        "splits the user couldn't have enumerated. Issues do not need 1:1 "
        "mapping to bullets.\n\n"
        "**Exclusions are binding.** If BRAINDUMP names a cut list, non-goals, "
        "out-of-scope section, or defers items to a later phase, those items "
        "MUST NOT be filed as issues — even if the codebase, audit findings, "
        "or other docs argue they're needed. Exclusions are the *only* hard "
        "scope rule from BRAINDUMP; additive expansion to deliver stated intent "
        "is the planner's judgment call.\n\n"
        "**Other docs are reference, not scope.** ROADMAP, ARCHITECTURE, DESIGN, "
        "CLAUDE, audits, ADRs — read them on demand to shape *how* an issue is "
        "written (fit existing systems, respect constraints). They never "
        "override BRAINDUMP exclusions.\n\n"
        "**Discovery and research are complete.** Pre-built artifacts live at "
        "`.aidlc/discovery/findings.md` (current repo state for BRAINDUMP-relevant "
        "systems) and `.aidlc/research/*.md` (per-topic answers). Reference them "
        "in issue descriptions when relied on. The `research` planning action "
        "has been removed; if a topic is missing, read the file directly with "
        "your tools.\n\n"
        "**Planning is complete when the filed-or-prior issue set is sufficient "
        "to deliver every BRAINDUMP intent — including discovered prereq/infra "
        'work. "Sufficient" not "literal coverage."**\n',
        f"\n### BRAINDUMP.md (full content)\n```\n{content}\n```",
    ]


def _render_discovery_section(planner) -> list[str]:
    """Point the planner at discovery + research artifacts; do NOT embed them.

    The model has file-read tools; embedding hundreds of KB of pre-built
    findings into every cycle's prompt is wasteful (cache_read tokens,
    prompt budget) and brittle (a partial / killed discovery run can
    write garbage that then explodes the prompt). Listing the files here
    gives the model a known location to read when it actually needs that
    context, and keeps the prompt small.
    """
    aidlc_dir = Path(planner.config["_aidlc_dir"])
    findings_path = aidlc_dir / "discovery" / "findings.md"
    research_dir = aidlc_dir / "research"

    if not findings_path.exists() and not research_dir.exists():
        return []

    lines = [
        "\n## Discovery & Research (read on demand)\n",
        "Pre-built artifacts from earlier phases. Use your file tools to read "
        "the ones relevant to the current cycle — do not re-derive their content.",
    ]

    if findings_path.exists():
        lines.append("- `.aidlc/discovery/findings.md` — repo state for BRAINDUMP-relevant systems")

    if research_dir.exists():
        research_files = sorted(p.name for p in research_dir.glob("*.md"))
        for name in research_files:
            lines.append(f"- `.aidlc/research/{name}`")

    return lines


def _enforce_prompt_budget(prompt: str, planner) -> str:
    """Shrink planning prompt to configured budget while preserving key instructions.

    Drop priority (first to last):
      1. ## Existing Issues (current-run)
      2. ## Prior Run — Already Done (prior-run issues from disk)
      3. ## Previous Cycle
      4. ## BRAINDUMP — Intent Source (last-resort drop; pointer is left behind)
    Schema, instructions, and Run State are never dropped.
    """
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
        r"\n## Prior Run[\s\S]*?(?=\n## |\Z)",
        "\n## Prior Run — Already Done\nPrior issues exist on disk; read .aidlc/issues/*.md for the full set.",
        shrunk,
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

    shrunk = re.sub(
        r"\n## BRAINDUMP — Intent Source \(authoritative\)[\s\S]*?(?=\n## |\Z)",
        "\n## BRAINDUMP — Intent Source (authoritative)\nRead BRAINDUMP.md at the project root for the full intent source. Translate intent into issues (1:N OK; file infra/prereq work as needed); cut-list items are forbidden.",
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

    lines.append("## Intent Source (authoritative)")
    lines.append("- BRAINDUMP.md")
    lines.append("")

    optional_refs = [
        "README.md",
        "STATUS.md",
        "ROADMAP.md",
        "ARCHITECTURE.md",
        "DESIGN.md",
        "CLAUDE.md",
    ]
    present = [n for n in optional_refs if (planner.project_root / n).exists()]
    if present:
        lines.append("## Reference Docs (optional context — never expand scope)")
        for name in present:
            lines.append(f"- {name}")
        lines.append("")

    aidlc_dir = Path(planner.config["_aidlc_dir"])
    discovery_findings = aidlc_dir / "discovery" / "findings.md"
    discovery_topics = aidlc_dir / "discovery" / "topics.json"
    if discovery_findings.exists() or discovery_topics.exists():
        lines.append("## Discovery (pre-built — current repo state)")
        if discovery_findings.exists():
            lines.append("- .aidlc/discovery/findings.md")
        if discovery_topics.exists():
            lines.append("- .aidlc/discovery/topics.json")
        lines.append("")

    research_dir = aidlc_dir / "research"
    if research_dir.exists():
        research_files = sorted(research_dir.glob("*.md"))
        if research_files:
            lines.append("## Research (pre-built — answers to discovery topics)")
            for rf in research_files:
                lines.append(f"- .aidlc/research/{rf.name}")
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
        active_issues = [
            issue for issue in issues if issue.get("status", "pending") in active_statuses
        ]
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
            "Read: `BRAINDUMP.md` (rendered below — authoritative scope), the repo "
            "itself, `.aidlc/planning_index.md`, `.aidlc/issues/*.md`. Other docs "
            "(README, ROADMAP, ARCHITECTURE, audits, ADRs) are reference only — "
            "pull on demand. Full file access — do not ask for pastes."
        ),
    ]

    if planner._last_cycle_notes:
        max_prev_notes = max(
            100, int(planner.config.get("planning_last_cycle_notes_max_chars", 500))
        )
        volatile_parts.append("\n## Previous Cycle\n")
        volatile_parts.append(planner._last_cycle_notes[:max_prev_notes])

    # Existing (current-run) issues: dropped first under budget pressure.
    volatile_parts.extend(_render_existing_issues_section(planner))
    # Prior-run issues from disk: also dropped first under budget pressure
    # (see _enforce_prompt_budget). ISSUE-005.
    volatile_parts.extend(_render_prior_run_issues_section(planner))
    # Foundation docs: dropped 3rd. ISSUE-006.
    volatile_parts.extend(_render_foundation_docs_section(planner))
    # Discovery + research artifacts (pre-built): dropped under same budget pressure.
    volatile_parts.extend(_render_discovery_section(planner))

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
            f"Last planning cycle ({cycle_num}) assessment: {frontier}\nNotes: {notes}"
        ),
    }
    notes_path.write_text(json.dumps(data, indent=2))
