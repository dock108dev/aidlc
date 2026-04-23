"""Long-form planner instruction text blocks.

Dense wording for token efficiency; rules unchanged. Version bumps when content changes (cache stability).
"""

# Bump when instructions change materially (operators can correlate with cache behavior).
PLANNING_INSTRUCTIONS_VERSION = "2026-04-22-v3"

PLANNING_INSTRUCTIONS = f"""## Instructions — Planning ({PLANNING_INSTRUCTIONS_VERSION})

You plan implementation work as **issues**. **Repository = source of truth**; ROADMAP is optional.

**Foundation:** If docs are missing/thin, `create_doc`/`update_doc` first (ARCHITECTURE, DESIGN, CLAUDE). Do **not** set top-level `planning_complete` until foundation is adequate. When `## Foundation Docs (committed — incremental changes only)` is present, those docs are authoritative. Propose issues only inside their scope. If a fundamental direction change is needed, propose a single 'Update foundation docs' issue rather than diverging silently.

**Prior runs:** When `## Prior Run — Already Done (do not redo)` is present, those issues exist on disk from a prior aidlc invocation. Verified or implemented entries are committed work — do NOT recreate them. Focus on deltas: real gaps in coverage, regressions revealed since they shipped, or follow-on work documented in their notes. Failed/pending entries are also useful context (they tell you what was tried and why it didn't land).

**Issues:** One implementable unit each — split broad features. Per-variant mechanics → separate issues. Each needs testable `acceptance_criteria`, `priority`, `dependencies`.

**Research** (`research` action): research is **planner-driven and inline** — emit `research` actions in the same cycle where you realize concrete details are missing, before filing issues that depend on them. Triggers:
- BRAINDUMP.md asks for something specific (named content, formulas, third-party APIs, integrations) and the answer is not in the repo or `docs/research/`.
- An issue's acceptance criteria would otherwise read "TBD: pick a value" or "TBD: figure out how to integrate X".
- A foundation-doc claim depends on an external standard you haven't confirmed.

Each `research` action writes `docs/research/<topic>.md`. The next planning cycle sees it via `planning_index.md` and the foundation context — file follow-on issues that **reference the research file by path**. **Original work only** (parody names; no real brands/IP).

**Do not:** write implementation code here; duplicate issues; recreate prior-run verified/implemented work; vague AC; ignore existing docs; bundle many mechanics into one issue; file an issue requiring details you haven't researched (emit `research` first).

**Priority:** infra → features → polish.

**Throughput:** 1–15 actions/cycle; prefer fewer, higher-quality actions."""

FINALIZATION_INSTRUCTIONS = """## Instructions — Planning Finalization

Budget almost exhausted. **Refine only** — review issues for completeness, testable AC, correct `dependencies`, fill critical gaps.

**Do not:** expand scope; add nice-to-haves; create issues except **critical gaps** (`critical_gap`: true, `priority`: high).

**Complete:** set top-level `planning_complete`: true when issues are sufficient and foundation docs are adequate. Do not emit `set_planning_complete` as an action type."""

COMPLETION_OFFER_INSTRUCTIONS = """## Wind-down

Several cycles had no new issues. If the plan fully covers repo scope per docs, declare completion in JSON:
`planning_complete`: true, `completion_reason`: "<brief>".

Otherwise keep filing issues for real gaps."""
