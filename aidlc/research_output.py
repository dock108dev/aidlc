"""Utilities for validating and repairing research model output."""

import re

_PERMISSION_CHATTER_PATTERNS = (
    re.compile(r"\bwrite tool needs your permission\b", re.IGNORECASE),
    re.compile(r"\bapprove the write permission\b", re.IGNORECASE),
    re.compile(r"\bneeds your write permission\b", re.IGNORECASE),
    # Match both the new `.aidlc/research/*.md` path and the legacy
    # `docs/research/*.md` path (the model may quote either when chatting
    # about permissions). The detection is for permission-meta responses,
    # not for path enforcement.
    re.compile(r"\btrying to save .*(?:\.aidlc|docs)/research/.*\.md\b", re.IGNORECASE),
    re.compile(r"\bin the meantime, here's a summary\b", re.IGNORECASE),
)


def is_permission_chatter(output: str) -> bool:
    """Return True when output looks like a write-permission meta response."""
    text = (output or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _PERMISSION_CHATTER_PATTERNS)


def add_research_output_constraints(prompt: str) -> str:
    """Append constraints that force plain markdown output."""
    suffix = (
        "\n\n## Critical Output Constraints\n"
        "- You are NOT allowed to use tools.\n"
        "- Do NOT attempt to write files.\n"
        "- Do NOT ask for write permissions.\n"
        "- Do NOT mention tools, permissions, or inability to save files.\n"
        "- Return ONLY the research markdown document body.\n"
    )
    return f"{prompt.rstrip()}{suffix}"


def build_repair_prompt(topic: str, question: str, bad_output: str) -> str:
    """Build a repair prompt after receiving permission-chatter output."""
    excerpt = (bad_output or "").strip()
    if len(excerpt) > 12000:
        excerpt = excerpt[:12000] + "\n\n... (truncated)"
    return (
        f"# Research: {topic}\n\n"
        "You previously returned a message about write permissions instead of the "
        "requested research document.\n\n"
        "Regenerate the FULL research document now.\n\n"
        "## Question\n"
        f"{question}\n\n"
        "## Requirements\n"
        "- Provide complete, concrete, implementation-ready guidance.\n"
        "- Use markdown headings and structured sections.\n"
        "- Do NOT mention tools, permissions, file writes, or summaries.\n"
        "- Return ONLY the markdown document body.\n\n"
        "## Previous invalid response (for context)\n"
        "```markdown\n"
        f"{excerpt}\n"
        "```"
    )
