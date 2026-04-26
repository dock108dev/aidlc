"""Research phase — runs the topic list produced by discovery.

Reads `docs/discovery/topics.json`, executes one research call per topic,
writes `docs/research/<topic>.md` per entry. Skip-if-exists per topic.

The per-topic execution logic was lifted from the legacy
`planner_helpers.execute_research` so the planner no longer juggles
investigation alongside issue creation.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from .discovery_prompt import sanitize_topic_slug
from .research_output import (
    add_research_output_constraints,
    build_repair_prompt,
    is_permission_chatter,
)

_PER_TOPIC_INSTRUCTIONS = [
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
    "If this is repo archaeology (current behavior, call graphs, contracts):",
    "- Map the existing surface concretely (files, symbols, signals, data shapes)",
    "- Cite the file paths and the specific functions/classes that matter",
    "- Note what's stubbed, what's wired, what's missing",
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
    "The document should:",
    "- Answer the research question with specific, actionable content",
    "- Reference relevant code sections if scope files were provided",
    "- Identify trade-offs between alternatives",
    "- Provide concrete implementation guidance",
    "- Include formulas, algorithms, or design patterns as applicable",
    "",
    "Output your response as a markdown document. No JSON wrapping needed.",
]


# Source file extensions worth listing when scope is a directory. Keeps the
# model's "look in this area" signal intact without dumping every binary.
_DIR_SCOPE_EXTENSIONS = {
    ".py",
    ".gd",
    ".gdshader",
    ".tscn",
    ".tres",
    ".cfg",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".swift",
    ".lua",
    ".sh",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
}


def _list_dir_scope(dir_path: Path, project_root: Path, remaining_budget: int) -> list[str]:
    """List source-relative paths under a directory, capped at remaining_budget.

    Walks recursively but skips hidden dirs and common noise (node_modules,
    .git, __pycache__). Returns paths relative to project_root, sorted for
    cache stability.
    """
    skip_names = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".aidlc"}
    collected: list[Path] = []
    for child in sorted(dir_path.rglob("*")):
        if any(part.startswith(".") or part in skip_names for part in child.parts):
            continue
        if not child.is_file():
            continue
        if child.suffix.lower() not in _DIR_SCOPE_EXTENSIONS:
            continue
        collected.append(child)
        if len(collected) >= remaining_budget:
            break
    rels: list[str] = []
    for p in collected:
        try:
            rels.append(str(p.relative_to(project_root)))
        except ValueError:
            rels.append(str(p))
    return rels


def _build_topic_prompt(
    topic: str,
    question: str,
    scope: list[str],
    project_root: Path,
    max_files: int,
    max_chars: int,
    logger: logging.Logger,
) -> str:
    parts = [f"# Research: {topic}", "", "## Question", question, ""]
    scope_blocks: list[str] = []
    dir_listings: list[tuple[str, list[str]]] = []
    files_used = 0
    for scope_path in (scope or [])[:max_files]:
        if files_used >= max_files:
            break
        full_path = project_root / scope_path
        if not full_path.exists():
            logger.warning(f"Scope path not found: {scope_path}")
            continue
        if full_path.is_file():
            try:
                content = full_path.read_text(errors="replace")
                if len(content) > max_chars:
                    content = content[:max_chars] + "\n\n... (truncated)"
                scope_blocks.append(f"### {scope_path}\n```\n{content}\n```")
                files_used += 1
            except OSError:
                logger.warning(f"Could not read scope file: {scope_path}")
        elif full_path.is_dir():
            remaining = max(0, max_files - files_used)
            listing = _list_dir_scope(full_path, project_root, remaining)
            if listing:
                dir_listings.append((scope_path, listing))
                files_used += len(listing)
            else:
                logger.info(f"Scope directory has no recognised source files: {scope_path}")
        else:
            logger.warning(f"Scope path is neither file nor directory: {scope_path}")

    if scope_blocks:
        parts.append("## Relevant Source Files\n")
        parts.extend(scope_blocks)
        parts.append("")
    if dir_listings:
        parts.append("## Scope Directories (read on demand — full file tools available)\n")
        for dir_path, files in dir_listings:
            parts.append(f"### {dir_path} ({len(files)} files)")
            for rel in files:
                parts.append(f"- {rel}")
            parts.append("")
    parts.extend(_PER_TOPIC_INSTRUCTIONS)
    return add_research_output_constraints("\n".join(parts))


def execute_research_topic(
    topic: str,
    question: str,
    scope: list[str],
    cli,
    project_root: Path,
    run_dir: Path,
    state,
    config: dict,
    logger: logging.Logger,
) -> bool:
    """Run one research topic. Returns True if a file was written, False otherwise.

    Skip-if-exists semantics: if `docs/research/<slug>.md` already exists, no
    model call is made and False is returned.
    """
    sanitized = sanitize_topic_slug(topic)[:80]
    sanitized = re.sub(r"-+", "-", sanitized).strip("-") or "topic"
    research_dir = project_root / "docs" / "research"
    output_path = research_dir / f"{sanitized}.md"
    if output_path.exists():
        logger.info(f"Research already exists: docs/research/{sanitized}.md — skipping")
        return False

    logger.info(f"Researching: {topic}")
    max_files = int(config.get("research_max_scope_files", 10))
    max_chars = int(config.get("research_max_source_chars", 15000))
    prompt = _build_topic_prompt(topic, question, scope, project_root, max_files, max_chars, logger)

    start = time.time()
    result = cli.execute_prompt(prompt, project_root)
    state.record_provider_result(result, config, phase="research")
    duration = time.time() - start
    state.elapsed_seconds += duration

    if not result.get("success"):
        logger.error(f"Research failed for {topic}: {result.get('error')}")
        return False

    output = result.get("output") or ""
    if not output:
        logger.warning(f"Research returned empty output for {topic}")
        return False

    if is_permission_chatter(output):
        logger.warning(
            "Research output requested write permissions; retrying with stricter constraints"
        )
        retry_prompt = build_repair_prompt(topic, question, output)
        retry_start = time.time()
        retry_result = cli.execute_prompt(retry_prompt, project_root)
        state.record_provider_result(retry_result, config, phase="research")
        state.elapsed_seconds += time.time() - retry_start
        if not retry_result.get("success") or not retry_result.get("output"):
            logger.error(f"Research retry failed for {topic}: {retry_result.get('error')}")
            return False
        output = retry_result["output"]
        if is_permission_chatter(output):
            logger.error(
                f"Research output for {topic} still contains permission chatter; skipping write"
            )
            return False

    research_dir.mkdir(parents=True, exist_ok=True)
    full_content = (
        f"# Research: {topic}\n\n"
        "*Auto-generated by AIDLC research phase*\n\n"
        f"**Question:** {question}\n\n"
        "---\n\n"
        f"{output}"
    )
    output_path.write_text(full_content, encoding="utf-8")

    state.files_created += 1
    state.created_artifacts.append(
        {
            "path": f"docs/research/{sanitized}.md",
            "type": "research",
            "action": "create",
        }
    )

    outputs_dir = run_dir / "claude_outputs"
    outputs_dir.mkdir(exist_ok=True)
    (outputs_dir / f"research_{sanitized}.md").write_text(output, encoding="utf-8")

    logger.info(f"Research complete: docs/research/{sanitized}.md")
    return True


def _load_topics(project_root: Path, logger: logging.Logger) -> list[dict]:
    topics_path = project_root / "docs" / "discovery" / "topics.json"
    if not topics_path.exists():
        logger.info("No discovery topics.json present — skipping research phase.")
        return []
    try:
        raw = json.loads(topics_path.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Could not load discovery topics.json: {exc}")
        return []
    if not isinstance(raw, list):
        logger.warning("discovery/topics.json is not a JSON array; ignoring.")
        return []
    valid: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        topic = str(entry.get("topic", "")).strip()
        question = str(entry.get("question", "")).strip()
        if not topic or not question:
            continue
        scope_raw = entry.get("scope") or []
        if not isinstance(scope_raw, list):
            scope_raw = []
        scope = [str(p).strip() for p in scope_raw if str(p).strip()]
        valid.append({"topic": topic, "question": question, "scope": scope})
    return valid


def run_research_phase(
    state,
    config: dict,
    cli,
    project_root: Path,
    run_dir: Path,
    logger: logging.Logger,
) -> int:
    """Run all discovery-produced research topics. Returns count written."""
    topics = _load_topics(project_root, logger)
    state.research_topics_total = len(topics)
    if not topics:
        state.research_topics_completed = 0
        return 0

    # No hard cap on topic count by default — discovery's job is to nominate
    # exactly the topics that need answering, and the research phase runs the
    # full list. Operators can still set `research_phase_max_topics` to a
    # positive integer as a safety net for runaway discoveries; default is 0
    # (unlimited).
    cap = int(config.get("research_phase_max_topics", 0) or 0)
    if cap > 0 and len(topics) > cap:
        logger.warning(
            f"Discovery proposed {len(topics)} topics; capping at {cap} (research_phase_max_topics)."
        )
        topics = topics[:cap]
    else:
        logger.info(f"Researching {len(topics)} discovery-nominated topic(s).")

    cli.set_phase("research")
    written = 0
    for entry in topics:
        try:
            if execute_research_topic(
                entry["topic"],
                entry["question"],
                entry["scope"],
                cli,
                project_root,
                run_dir,
                state,
                config,
                logger,
            ):
                written += 1
        except Exception as exc:  # noqa: BLE001 — keep loop going on per-topic failure
            logger.exception(f"Unexpected error researching '{entry['topic']}': {exc}")

    state.research_topics_completed = written
    logger.info(f"Research phase complete: {written}/{len(topics)} topics written this run.")
    return written
