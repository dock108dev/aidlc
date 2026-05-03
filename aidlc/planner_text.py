"""Long-form planner instruction text blocks.

Dense wording for token efficiency; rules unchanged. Version bumps when content changes (cache stability).
"""

from dataclasses import dataclass

# Bump when instructions change materially (operators can correlate with cache behavior).
PLANNING_INSTRUCTIONS_VERSION = "2026-05-03-v10"

PLANNING_INSTRUCTIONS = f"""## Instructions — Planning ({PLANNING_INSTRUCTIONS_VERSION})

You plan implementation work as **issues**. Inputs: the repo (read on demand), **BRAINDUMP.md** (the owner's intent for this cycle, rendered in full under `## BRAINDUMP — Intent Source`), and pre-built discovery + research artifacts (`.aidlc/discovery/findings.md` + `.aidlc/research/*.md`, listed under `## Discovery & Research`). The investigation work is already done — your job is to translate intent + findings into the right set of issues.

**BRAINDUMP is intent, not spec.** BRAINDUMP.md captures the owner's desired outcome from a black-box user perspective — what they want to be true after this cycle, not the implementation steps to get there. Translate intent into the real set of implementation issues by consulting `.aidlc/discovery/findings.md` (current repo state) and `.aidlc/research/*.md` (concrete answers to investigation questions). Issues do *not* need to map 1:1 to BRAINDUMP bullets. One bullet may produce several issues (split per concern, per file, per layer). Several bullets may share one prerequisite issue. File infra/prereq/cleanup issues the user didn't enumerate, when achieving a stated intent requires them.

**BRAINDUMP exclusions are binding.** Cut lists, non-goals, out-of-scope sections, deferred-to-later-phase items: forbidden scope this run. Reasonable-sounding work the codebase or other docs would justify is still out if BRAINDUMP excludes it. Additive scope is the planner's call; subtractive scope is BRAINDUMP's call.

**Other docs are reference, not scope.** ROADMAP / ARCHITECTURE / DESIGN / CLAUDE / audits / ADRs shape *how* an issue is written (fit existing systems, respect constraints). They never override BRAINDUMP exclusions. Audit findings about current state are inputs to BRAINDUMP-driven work.

**Findings vs BRAINDUMP — what each is for.** BRAINDUMP is the source for **what** the owner wants — the customer's vision and scope. `findings.md` and `.aidlc/research/*.md` are reference material for **how**: details, file locations, best practices, options to choose between, things in the BRAINDUMP that need more detail. They are *not* a verdict that work is or isn't needed. Do not collapse "BRAINDUMP says rebuild X" into "no issue needed" because findings reports X exists with correct dimensions; findings tells you where X lives so the rebuild issue can name the right files. The bar is "does the *composed result* match BRAINDUMP intent?", not "does each individual file already exist?".

**Read the BRAINDUMP's language to pick the mode.**
- *Vision / big-build / redesign language* — "rebuild", "big bang", "recompose", "stop patching symptoms", "redesign around one coherent design", "this pass is bigger", broad qualitative complaints, enumerated phases / steps / acceptance tests / zones — means **scaffold the full work** the BRAINDUMP describes. Expect to file many issues, including ones for systems findings says already "exist", because the owner is asking for composition / experience / coherence, not just file presence. Lean toward exploration: it is OK to file scaffolding issues that you'll deepen with details across later cycles.
- *Scoped-fix language* — a short list of named bugs, "fix X", "ship Y", concrete deliverables with no redesign rhetoric — means **a tight, focused set of implementation issues** matching the named work. Don't pad scope.
The BRAINDUMP's tone tells you which mode this run is in. Honor it.

**Issues are living.** Across cycles, as you read more of the repo, more research, and refine your model of what BRAINDUMP wants, **update existing issues** — add file references, options, expanded acceptance criteria, dependency edges. Don't lock issue text on cycle 1. `update_issue` actions are first-class. The bar for "good issue" is awareness and visibility into everything the implementer will need; not minimum word count.

**Prior runs:** When `## Prior Run — Already Done (do not redo)` is present, those issues exist on disk from a prior aidlc invocation. Verified or implemented entries are committed work — do NOT recreate them. Focus on deltas: BRAINDUMP intent still uncovered, follow-on work in their notes.

**Issues:** One implementable unit each — split broad features. Per-variant mechanics → separate issues. Each needs testable `acceptance_criteria`, `priority`, `dependencies`. Issue descriptions should reference the relevant `.aidlc/discovery/findings.md` section or `.aidlc/research/*.md` file when the planner relied on them.

**Discovery and research are complete.** Do NOT propose investigation as a planning action — the `research` action type has been removed. If a topic is missing, read the file directly with your tools and write the issue based on what you find; the discovery phase already nominated the topics it could.

**Do not:** write implementation code here; duplicate issues; recreate prior-run verified/implemented work; bundle many mechanics into one issue; use vague AC; file `create_doc`/`update_doc`/`research` actions (those action types are removed); file speculative future work BRAINDUMP didn't ask for or imply. The test for an additive issue is "does delivering BRAINDUMP intent require this?" not "would this be nice?"

**Priority:** infra → features → polish.

**Sizing follows mode.** Don't default to "fewer issues = better". Match the BRAINDUMP. A scoped-fix BRAINDUMP with 3–5 named bugs gets 3–6 issues. A vision / big-build BRAINDUMP enumerating phases / steps / acceptance tests / zones gets one issue per concrete deliverable, plus prereq/infra/cleanup issues you discover along the way — easily 10–20+ when the BRAINDUMP is genuinely a redesign pass. If you find yourself collapsing a vision BRAINDUMP into a small set of differential fixes against findings, stop and re-read the BRAINDUMP's tone — you're probably under-decomposing.

**Throughput:** up to 15 actions/cycle (create + update). It is fine for early cycles to scaffold many issues with skeletal detail and later cycles to deepen them with file references, options, and AC.

**Do not set `planning_complete` on normal cycles.** The system only interprets that flag during the dedicated VERIFY pass (the cycle whose instructions begin with `## VERIFY MODE — Final Coverage Check`, scheduled automatically after a cycle that files no new issues). On every other cycle, omit it or keep it false — otherwise the runner ignores it and you add noise."""

FINALIZATION_INSTRUCTIONS = """## Instructions — Planning Finalization

Budget almost exhausted. **Refine only** — review issues for completeness, testable AC, correct `dependencies`, fill critical gaps.

**Do not:** expand scope; add nice-to-haves; create issues except **critical gaps** (`critical_gap`: true, `priority`: high). A "critical gap" is intent that won't be deliverable from the current issue set — either an uncovered BRAINDUMP ask, or a discovered prereq/infra issue without which a covered ask can't ship.

**Do not** set top-level `planning_complete` here — that flag is **only** read on the VERIFY MODE cycle (see schema). In finalization, output refinements via `update_issue` (and rare `critical_gap` `create_issue` only); sufficiency is judged on the next verify or empty-cycle rules, not via this field. Do not emit `set_planning_complete` as an action type."""


@dataclass(frozen=True)
class Facet:
    """A single product-feedback lens used to scope a planning cycle.

    Each facet runs as its own cycle after the general pass. The facet's
    checklist tells the model what to look for under this lens; the
    exclusion line tells it what belongs to other facets and should be
    left alone (each of those gets its own cycle).
    """

    name: str
    slug: str
    checklist: tuple[str, ...]
    exclusion: str


# Fixed product-feedback taxonomy. BRAINDUMPs in this codebase are black-box
# user feedback about how the product works — these lenses match that, not
# engineering-quality lenses (security/perf/ops/a11y) that don't fit.
# Order matters: functionality first (what's broken / missing for intent to
# ship), then usability (friction), then design (coherence), then explicit
# new feature asks (often build on top of the above).
FACETS: tuple[Facet, ...] = (
    Facet(
        name="Functionality",
        slug="functionality",
        checklist=(
            "Behavior the BRAINDUMP calls out as broken, wrong, or unreliable.",
            "Missing pieces required for the named intent to be deliverable end-to-end "
            "(silent gaps the owner expects but didn't enumerate).",
            "Edge cases / failure modes implied by the intent (empty inputs, errors, "
            "partial state) that no existing issue covers.",
            "Cross-feature interactions the BRAINDUMP implies should work together.",
            "Functional acceptance criteria missing from existing issues.",
        ),
        exclusion=(
            "Friction, confusion, layout, look-and-feel, and explicit new-feature asks "
            "belong to other facets — leave them alone here."
        ),
    ),
    Facet(
        name="Usability & flow",
        slug="usability",
        checklist=(
            "Friction points, dead-ends, and surprising interactions when a user tries "
            "to do the thing the BRAINDUMP describes.",
            "Confusing labels, error messages, defaults, or modes.",
            "First-run / discoverability — can someone new figure it out without a guide; "
            "are sensible defaults in place.",
            "Flow gaps — steps the user has to do that should be automatic, or vice versa.",
            "Onboarding and recovery paths (undo, back, retry).",
        ),
        exclusion=(
            "Pure functional bugs, visual / layout coherence, and net-new feature asks "
            "belong to other facets — leave them alone here."
        ),
    ),
    Facet(
        name="Design & visual coherence",
        slug="design",
        checklist=(
            "Look, layout, and visual consistency across surfaces / screens / modes.",
            "Polish and micro-interactions called out in the BRAINDUMP.",
            "Visual hierarchy: is the important thing prominent; is noise quieted.",
            "Theming / spacing / typography inconsistencies that fragment the experience.",
            "Cross-surface design language drift (the same control behaves or looks "
            "different in different places).",
        ),
        exclusion=(
            "Functional correctness, usability flow, and explicit new feature asks "
            "belong to other facets — leave them alone here."
        ),
    ),
    Facet(
        name="New features (explicit asks)",
        slug="new_features",
        checklist=(
            "Net-new capabilities the BRAINDUMP names directly ('add X', 'I want Y').",
            "Capabilities required to make a stated new feature actually usable "
            "(prereq toggles, settings, surfaces).",
            "Acceptance criteria for new-feature issues — does shipping the AC actually "
            "deliver what the BRAINDUMP asked for.",
            "Dependencies between new-feature work and existing functionality.",
            "Cleanup / removal of obsolete behavior that the new feature replaces.",
        ),
        exclusion=(
            "Bugs, friction, and visual coherence on existing features belong to other "
            "facets — leave them alone here, even if they show up next to a new-feature "
            "ask."
        ),
    ),
)


def _format_facet_checklist(facet: Facet) -> str:
    bullets = "\n".join(f"- {item}" for item in facet.checklist)
    return f"{bullets}\n- {facet.exclusion}"


def planning_instructions_faceted(facet: Facet) -> str:
    """Wrap the standard PLANNING_INSTRUCTIONS with a facet-scoped header.

    The header tells the model:
      1. This cycle is scoped to a single product lens.
      2. What to look for under that lens (checklist).
      3. To leave the other facets alone — they each get their own cycle.
      4. Strongly prefer ``update_issue`` to enrich existing issues with this
         facet's concerns, rather than creating parallel issues.
    """
    checklist = _format_facet_checklist(facet)
    header = f"""## Faceted Planning Cycle — Scope: {facet.name}

This cycle is **scoped to a single product-feedback lens: {facet.name}**. The general planning pass already ran. Each remaining facet (functionality, usability, design, new features) gets its own dedicated cycle — leave the other lenses alone here.

**Look for under this lens:**
{checklist}

**Strongly prefer `update_issue` over `create_issue` on this cycle.** Most {facet.name.lower()} concerns will fit on issues already filed by the general pass — enrich those issues with facet-specific acceptance criteria, file references, edge cases, and notes. Only `create_issue` for genuine standalone gaps under this lens that don't belong on any existing issue.

**Do not** re-file or duplicate concerns the general pass already captured. **Do not** stretch into other facets to pad output. A productive facet cycle can be 0–6 actions; the cap is 15 but the goal is coverage, not volume.

---

"""
    return header + PLANNING_INSTRUCTIONS


VERIFY_INSTRUCTIONS = """## VERIFY MODE — Final Coverage Check

The general pass and all faceted cycles (functionality, usability, design, new features) have already run and the most recent cycle proposed no new issues. Before planning is declared complete, run this explicit coverage check.

**Walk this checklist with your file tools:**

1. Re-read `BRAINDUMP.md` (project root). First, classify the **mode** — vision / big-build / redesign vs scoped-fix — by the BRAINDUMP's tone. Then enumerate every distinct intent item (phases, numbered steps, acceptance tests, must-have lines, named zones / subsystems, "Required X" sections).
2. Read `.aidlc/discovery/findings.md`. Note every system / file that BRAINDUMP intent touches. Remember findings is reference for *how*, not a verdict on *whether* work is needed.
3. Skim `.aidlc/research/*.md` filenames listed under `## Discovery & Research`. Read any whose answers might imply work not yet captured.
4. Cross-reference against the existing issue set (the `## Existing Issues` and `## Prior Run` sections plus `.aidlc/issues/*.md` on disk).

**Mode-aware coverage check (run before declaring complete):**

- *Vision / big-build / redesign mode.* The bar is: have you scaffolded the full set of work the BRAINDUMP is describing — including the parts findings says are "fine" but BRAINDUMP wants reshaped, and including prereq / infra / cleanup work that surfaces from the redesign? If the existing issues read like differential bug fixes against findings while BRAINDUMP asks for composition / coherence / experience, you are under-covered. File the missing scaffolding issues. If existing issues already cover the work but are skeletal, prefer `update_issue` to deepen them rather than declaring complete prematurely.
- *Scoped-fix mode.* The bar is: is each named bug / deliverable in the BRAINDUMP filed as an issue? Don't pad scope.
- In both modes: when one issue genuinely covers multiple intent items, that's fine — but `completion_reason` must name the mapping with issue IDs, not just claim "everything is covered".

**Facet coverage check (in addition to the mode-aware check):**

The faceted cycles already had a chance to enrich existing issues and file gaps under each lens. Now sanity-check the result across all four facets:

- *Functionality.* Are broken / wrong / missing behaviors from the BRAINDUMP all captured?
- *Usability & flow.* Are the friction points, confusing flows, and first-run gaps either filed or merged into a relevant issue's acceptance criteria?
- *Design & visual coherence.* If BRAINDUMP raises any visual / layout / consistency concern, is it on an issue (its own or rolled in)?
- *New features.* Is every explicit net-new ask filed as its own issue with concrete acceptance criteria?

If a facet was not applicable to this BRAINDUMP (e.g., no design feedback at all), say so in `completion_reason` rather than silently skipping.

**For each BRAINDUMP intent item:**
- Covered by an existing issue (any status) → OK.
- Not covered → file a `create_issue` action with `critical_gap: true` and `priority: "high"`. Description must cite the BRAINDUMP line it satisfies and the discovery finding it relies on.

**For each significant system named in findings:**
- Touched by an existing/prior issue → OK.
- Structurally needed for an intent item but no issue mentions it → file a prereq `create_issue` (also `critical_gap: true`).

**If everything checks out:**
- Return `actions: []`.
- Set top-level `planning_complete: true`.
- Set `completion_reason` to a **concrete** statement that names the mode, the BRAINDUMP intent items, and the issues covering them, e.g.:
  `"Mode: vision/big-build. BRAINDUMP enumerates Phases 1-8 + Tests A-F (14 intent items). Coverage: ISSUE-001..ISSUE-006 cover Phases 1-3 (camera/bounds/movement), ISSUE-007..ISSUE-010 cover Phases 4-6 (interactions/HUD/overlays), ISSUE-011..ISSUE-014 cover Phases 7-8 + Tests A-F. Tests B and D bundled into ISSUE-013 (single test harness). No infra prereqs missing."`
  Vague claims like "all non-negotiables covered" without naming the mode and mapping are not accepted.

**Do not** file speculative work, future-phase polish, or anything BRAINDUMP excludes. The bar is: *would shipping every existing issue make BRAINDUMP intent true?* If yes, declare done. If a single intent item is uncovered, file just that issue (and only that issue) and let the next verify pass confirm."""
