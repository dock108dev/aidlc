"""Context preparation for large projects.

Solves the problem of 80k context budget vs 230k+ of project docs by:

1. Doc manifest — one-line summary of every doc (even ones that don't fit),
   so Claude knows what exists and can request research actions for docs it needs.

2. Project brief — Claude reads ALL docs in batches and produces a condensed
   ~15k char project brief capturing essential scope, mechanics, and requirements.

3. Repository-wide context synthesis — planning uses the full repository signal
   rather than assuming a single roadmap file is authoritative.
"""

from pathlib import Path


def build_doc_manifest(doc_files: list[dict], max_summary_len: int = 120) -> str:
    """Build a compact manifest of ALL docs with one-line summaries.

    This ensures Claude knows what documentation exists even when docs
    don't fit in the context budget. ~50 chars per doc = 2k for 40 docs.
    """
    lines = ["## Document Manifest\n"]
    lines.append(
        "All project documentation files (use 'research' action to read any you need):\n"
    )

    for doc in doc_files:
        path = doc["path"]
        size = doc["size"]
        # Extract first meaningful line as summary
        summary = _extract_summary(doc["content"], max_summary_len)
        lines.append(f"- `{path}` ({size:,} chars) — {summary}")

    return "\n".join(lines)


def build_project_brief(
    doc_files: list[dict],
    cli,
    project_root: Path,
    logger,
    max_brief_chars: int = 15000,
) -> str | None:
    """Have Claude read all docs and produce a condensed project brief.

    Sends docs in batches to Claude, asks for a structured summary.
    Returns the brief text, or None if Claude is unavailable.
    """
    if not cli:
        return None

    # Concatenate all doc content (for sending to Claude)
    all_content_parts = []
    total = 0
    for doc in doc_files:
        header = f"\n--- {doc['path']} ---\n"
        all_content_parts.append(header + doc["content"])
        total += len(header) + doc["size"]

    # If total docs fit in one prompt (~100k), send them all at once.
    # Otherwise batch into chunks.
    max_per_batch = 100000
    batches = []
    current_batch = []
    current_size = 0
    for part in all_content_parts:
        if current_size + len(part) > max_per_batch and current_batch:
            batches.append("\n".join(current_batch))
            current_batch = []
            current_size = 0
        current_batch.append(part)
        current_size += len(part)
    if current_batch:
        batches.append("\n".join(current_batch))

    # For single batch, ask for the full brief directly
    if len(batches) == 1:
        brief = _generate_brief_single(
            batches[0], cli, project_root, logger, max_brief_chars
        )
    else:
        # Multiple batches: summarize each, then combine
        batch_summaries = []
        for i, batch in enumerate(batches):
            logger.info(f"Summarizing doc batch {i + 1}/{len(batches)}...")
            summary = _summarize_batch(
                batch, i + 1, len(batches), cli, project_root, logger
            )
            if summary:
                batch_summaries.append(summary)

        if batch_summaries:
            combined = "\n\n---\n\n".join(batch_summaries)
            brief = _generate_brief_single(
                combined, cli, project_root, logger, max_brief_chars
            )
        else:
            brief = None

    return brief


# --- Internal helpers ---


def _extract_summary(content: str, max_len: int) -> str:
    """Extract a one-line summary from doc content."""
    for line in content.split("\n"):
        line = line.strip()
        # Skip empty lines, headers, frontmatter, comments
        if (
            not line
            or line.startswith("#")
            or line.startswith("---")
            or line.startswith("<!--")
        ):
            continue
        # Skip very short lines
        if len(line) < 10:
            continue
        # Found a content line — use it as summary
        if len(line) > max_len:
            return line[: max_len - 3] + "..."
        return line
    return "(empty or header-only document)"


def _generate_brief_single(
    content: str, cli, project_root: Path, logger, max_chars: int
) -> str | None:
    """Generate a project brief from a single batch of content."""
    prompt = f"""You are reading all the documentation for a software project.
Your job is to produce a condensed PROJECT BRIEF that captures everything
a planning agent needs to know to create a comprehensive implementation plan.

The brief must be under {max_chars} characters and cover:

1. **Project Identity** — What is this? What's the tech stack? What's the goal?
2. **Scope Summary** — All major features/systems, organized by phase or priority
3. **Architecture** — Key components, patterns, data flow
4. **Content/Data** — What types of content exist (items, stores, characters, etc.)
5. **Mechanics** — Core gameplay/business logic that needs implementation
6. **Technical Requirements** — Testing, CI, deployment, performance targets
7. **Key Design Decisions** — Important constraints or patterns to follow

Be specific and concrete. Include names, numbers, and details.
Do NOT be vague. If the docs describe 5 store types, list all 5 with their key mechanics.
If there are formulas or algorithms, include them.

Here are ALL the project documents:

{content}

Write the PROJECT BRIEF now. Output as markdown. No JSON wrapping."""

    result = cli.execute_prompt(prompt, project_root)
    if result["success"] and result.get("output"):
        brief = result["output"].strip()
        if len(brief) > max_chars:
            brief = brief[:max_chars] + "\n\n... (brief truncated)"
        return brief
    else:
        logger.warning(f"Failed to generate project brief: {result.get('error')}")
        return None


def _summarize_batch(
    content: str, batch_num: int, total_batches: int, cli, project_root: Path, logger
) -> str | None:
    """Summarize a batch of docs."""
    prompt = f"""You are reading batch {batch_num} of {total_batches} of documentation
for a software project. Summarize the key information from these documents.
Include all specific details: names, mechanics, formulas, requirements, constraints.
Be thorough — this summary will be used to create a project brief.

Documents in this batch:

{content}

Write a detailed summary of these documents. Output as markdown."""

    result = cli.execute_prompt(prompt, project_root)
    if result["success"] and result.get("output"):
        return result["output"].strip()
    else:
        logger.warning(f"Failed to summarize batch {batch_num}: {result.get('error')}")
        return None
