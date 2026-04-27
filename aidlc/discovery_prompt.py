"""Discovery prompt template + output parser.

Discovery is a single pre-planning model pass that produces:
  1. `.aidlc/discovery/findings.md` — current state of the systems BRAINDUMP
     references (what's wired, stubbed, missing) with file paths.
  2. `.aidlc/discovery/topics.json` — research topics the model couldn't answer
     from the scan alone (each: topic, question, scope[]).

This module owns the prompt and the strict output parser. Orchestration lives
in `discovery.py`.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

DISCOVERY_INSTRUCTIONS_VERSION = "2026-04-25-v3"

DISCOVERY_PROMPT_HEADER = f"""# Discovery (pre-planning, {DISCOVERY_INSTRUCTIONS_VERSION})

You are doing **pre-planning discovery** for an AIDLC run. Read BRAINDUMP.md
(below) and the project repo (you have file-read tools — use them). Produce
two artifacts that the planning phase will consume:

1. **Findings** — current state of every system BRAINDUMP touches. For each
   relevant system: what files implement it, what's wired, what's stubbed,
   what's missing. Cite file paths. This is factual reporting, not planning.
   **Anything you confidently know from the scan goes here, not into topics.**
2. **Research topics** — questions the planner will have to *guess at* if you
   don't answer them. The bar for nominating a topic is one question:
   *"would the planner have to guess if I didn't research this?"* If yes,
   it's a topic. If you (or BRAINDUMP, or an existing `.aidlc/research/*.md`)
   already have the answer, record it in findings.md and do NOT create a
   topic.

When you do nominate topics, decompose them properly — better many small
topics than one vague one (planner consumes them individually):
   - One topic per system/contract/integration that needs investigation.
   - Split a fat topic into subtopics when they touch different files,
     subsystems, or concerns.
   - Example: "tutorial-graph-shape", "tutorial-step-ui-mapping",
     "tutorial-skip-input-routing" — not one vague "tutorial-rewrite".

The number of topics you return **is** the number we research — there is no
hidden cap. So be exhaustive about *real unknowns*, and equally disciplined
about *not* nominating things you already know. Either answer is fine, but
each topic must clear the "would planning have to guess" bar.

Existing research files (already answered — do NOT re-nominate; cite by path
in findings.md if relevant) are listed under `## Existing Research` below.

Discovery is single-shot. Decompose real unknowns thoroughly; record
everything else in findings.md.
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
- `topic`: short kebab-case slug (used as filename `.aidlc/research/<topic>.md`).
- `question`: one specific question.
- `scope`: list of repo-relative file paths the research call should read first.
- Empty list `[]` is allowed if everything was answered from the scan.
"""


def build_discovery_prompt(
    braindump: str,
    repo_summary: str,
    existing_research: list[str] | None = None,
) -> str:
    """Assemble the discovery prompt.

    `repo_summary` is a short pointer block (file counts, top-level layout) — the
    model uses its own file tools for the real reading.

    `existing_research` is the list of `.aidlc/research/<slug>.md` filenames
    already on disk. Listing them in the prompt means the model trusts what's
    already answered and won't re-nominate those topics.
    """
    sections = [
        DISCOVERY_PROMPT_HEADER,
        "## BRAINDUMP.md (the owner's intent)\n```\n" + braindump.rstrip() + "\n```",
        "## Repo Summary\n" + repo_summary.rstrip(),
    ]
    if existing_research:
        listing = "\n".join(f"- .aidlc/research/{name}" for name in existing_research)
        sections.append("## Existing Research (already answered — do NOT re-nominate)\n" + listing)
    else:
        sections.append(
            "## Existing Research\n_(none on disk — every relevant unknown is fair game)_"
        )
    sections.append(DISCOVERY_OUTPUT_FORMAT)
    return "\n\n".join(sections)


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
