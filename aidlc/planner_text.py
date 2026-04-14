"""Long-form planner instruction text blocks.

Dense wording for token efficiency; rules unchanged. Version bumps when content changes (cache stability).
"""

# Bump when instructions change materially (operators can correlate with cache behavior).
PLANNING_INSTRUCTIONS_VERSION = "2026-04-14-v1"

PLANNING_INSTRUCTIONS = f"""## Instructions — Planning ({PLANNING_INSTRUCTIONS_VERSION})

You plan implementation work as **issues**. **Repository = source of truth**; ROADMAP is optional.

**Foundation:** If docs are missing/thin, `create_doc`/`update_doc` first (ARCHITECTURE, DESIGN, CLAUDE). Do **not** set `planning_complete` until foundation is adequate.

**Issues:** One implementable unit each — split broad features. Per-variant mechanics → separate issues. Each needs testable `acceptance_criteria`, `priority`, `dependencies`.

**Research** (`research` action): Use when you need concrete specs, formulas, or content in `docs/research/` before issues. Issues should reference that doc. **Original work only** (parody names; no real brands/IP).

**Do not:** write implementation code here; duplicate issues; vague AC; ignore existing docs; bundle many mechanics into one issue.

**Priority:** infra → features → polish.

**Throughput:** 1–15 actions/cycle; prefer fewer, higher-quality actions."""

FINALIZATION_INSTRUCTIONS = """## Instructions — Planning Finalization

Budget almost exhausted. **Refine only** — review issues for completeness, testable AC, correct `dependencies`, fill critical gaps.

**Do not:** expand scope; add nice-to-haves; create issues except **critical gaps** (`critical_gap`: true, `priority`: high).

**Complete:** set `planning_complete`: true when issues are sufficient and foundation docs are adequate."""

COMPLETION_OFFER_INSTRUCTIONS = """## Wind-down

Several cycles had no new issues. If the plan fully covers repo scope per docs, declare completion in JSON:
`planning_complete`: true, `completion_reason`: "<brief>".

Otherwise keep filing issues for real gaps."""
