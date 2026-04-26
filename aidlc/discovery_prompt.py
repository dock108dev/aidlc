"""Discovery prompt template + output parser.

Discovery is a single pre-planning model pass that produces:
  1. `docs/discovery/findings.md` — current state of the systems BRAINDUMP
     references (what's wired, stubbed, missing) with file paths.
  2. `docs/discovery/topics.json` — research topics the model couldn't answer
     from the scan alone (each: topic, question, scope[]).

This module owns the prompt and the strict output parser. Orchestration lives
in `discovery.py`.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

DISCOVERY_INSTRUCTIONS_VERSION = "2026-04-25-v1"

DISCOVERY_PROMPT_HEADER = f"""# Discovery (pre-planning, {DISCOVERY_INSTRUCTIONS_VERSION})

You are doing **pre-planning discovery** for an AIDLC run. Read BRAINDUMP.md
(below) and the project repo (you have file-read tools — use them). Produce
two artifacts that the planning phase will consume:

1. **Findings** — current state of every system BRAINDUMP touches. For each
   relevant system: what files implement it, what's wired, what's stubbed,
   what's missing. Cite file paths. This is factual reporting, not planning.
2. **Research topics** — questions you could not answer confidently from the
   scan alone. Examples: "How does the existing tutorial graph map step IDs
   to UI nodes?" / "Which Godot signal carries the player→shelf interact
   event today?" Each topic gets a short scope list (file paths to dig into).

If a BRAINDUMP item is fully understood from the scan, do **not** invent a
research topic for it. Only emit topics for genuine unknowns.

Discovery is single-shot. Be thorough.
"""

DISCOVERY_OUTPUT_FORMAT = """## Output Format (strict)

Return **exactly** this structure — markdown findings, then a JSON code-fence
with the topics array. No prose outside these two blocks.

```
# Findings

<your markdown findings — one section per system, with file paths>

```json
[
  {"topic": "tutorial-graph-shape", "question": "How is the current 11-step graph wired in game/systems/tutorial.gd?", "scope": ["game/systems/tutorial.gd", "game/scenes/tutorial.tscn"]},
  {"topic": "shelf-npc-signal", "question": "Which signal carries the shelf→NPC sale event today?", "scope": ["game/systems/sales.gd"]}
]
```
```

Topic rules:
- `topic`: short kebab-case slug (used as filename `docs/research/<topic>.md`).
- `question`: one specific question.
- `scope`: list of repo-relative file paths the research call should read first.
- Empty list `[]` is allowed if everything was answered from the scan.
"""


def build_discovery_prompt(braindump: str, repo_summary: str) -> str:
    """Assemble the discovery prompt.

    `repo_summary` is a short pointer block (file counts, top-level layout) — the
    model uses its own file tools for the real reading.
    """
    return "\n\n".join(
        [
            DISCOVERY_PROMPT_HEADER,
            "## BRAINDUMP.md (the owner's intent)\n```\n" + braindump.rstrip() + "\n```",
            "## Repo Summary\n" + repo_summary.rstrip(),
            DISCOVERY_OUTPUT_FORMAT,
        ]
    )


_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def parse_discovery_output(raw: str) -> tuple[str, list[dict]]:
    """Split the model output into (findings_markdown, topics_list).

    - `findings_markdown` is everything before the first ```json fence,
      with surrounding whitespace stripped. Always returned as a non-empty
      string when the model produced *any* markdown; empty string otherwise.
    - `topics_list` is the parsed JSON array (validated). On any parse error
      or schema mismatch, returns `[]` — orchestration logs a warning.
    """
    if not raw or not raw.strip():
        return "", []

    fence_match = _JSON_FENCE_RE.search(raw)
    if not fence_match:
        # No JSON fence — treat entire output as findings, no topics.
        return raw.strip(), []

    findings = raw[: fence_match.start()].strip()
    json_body = fence_match.group(1).strip()

    try:
        parsed = json.loads(json_body)
    except json.JSONDecodeError:
        return findings, []

    if not isinstance(parsed, list):
        return findings, []

    topics: list[dict] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        topic = str(entry.get("topic", "")).strip()
        question = str(entry.get("question", "")).strip()
        if not topic or not question:
            continue
        scope_raw = entry.get("scope", []) or []
        if not isinstance(scope_raw, Iterable):
            scope_raw = []
        scope = [str(p).strip() for p in scope_raw if str(p).strip()]
        topics.append({"topic": topic, "question": question, "scope": scope})

    return findings, topics


def sanitize_topic_slug(topic: str) -> str:
    """Same slug rules as the legacy research filename so artifacts line up."""
    return re.sub(r"[^a-z0-9_-]", "-", (topic or "").lower()).strip("-") or "topic"
