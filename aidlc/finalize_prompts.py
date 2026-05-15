"""Prompt templates for AIDLC finalization passes.

Each constant is a full prompt template with {placeholders} for project-specific
context injection. The Finalizer class fills these before sending to the
provider.

The cleanup pass assumes **edit permission is for changing the product**:
implement fixes and removals in the tree first; the markdown under
``docs/audits/`` records *what you changed* and any intentional non-fixes — it
must not read like a standalone audit you could have written without touching
files.

Every pass enforces the **Actionability Contract**: each finding must either be
fixed in-place during the pass OR documented as an intentional non-fix with
concrete justification, both in the pass report (``docs/audits/<pass>.md``)
and at the relevant code location (docstring or inline comment). Bare TODO /
follow-up notes are rejected — pick a side.
"""

# Available passes in canonical end-of-run order. Periodic cleanup runs a
# subset (see config: cleanup_passes_periodic).
PASS_ORDER = ["cleanup", "docs"]

PASS_DESCRIPTIONS = {
    "cleanup": "Code cleanup — delete dead code and consolidate in-repo; report mirrors changes",
    "docs": "Documentation consolidation — rewrite docs to reflect current reality",
}


# Reusable trailer appended to every pass prompt — keeps the contract
# consistent and easy to audit/update in one place.
ACTIONABILITY_CONTRACT = """\
## Actionability Contract (non-negotiable)

**What “done” means:** Follow this pass’s scope (see the pass instructions
above). For code passes, **the repo diff is the primary deliverable** — you
must apply real edits (hardening, deletions, tightening, consolidation) in
source and config files. The report at `docs/audits/{report_filename}` is a
*written record* of those edits and of any item you **intentionally** did not
change (with rationale). It is not acceptable to produce only prose in the
report while leaving the codebase unchanged when in-scope fixes were
feasible. For the **docs** pass only, “act” means editing markdown under
`docs/` and `README.md` as that prompt specifies — no unrelated code refactors.

Every finding gets exactly one of two outcomes:

1. **Act**: edit the file directly (delete, rewrite, tighten, redact). The
   change must leave the repo building and the test command passing if one is
   configured.
2. **Justify**: keep the code as-is *and* write a concrete justification in
   **both**:
   - the pass report (`docs/audits/{report_filename}`), and
   - a docstring or inline comment at the code location (one-liner is fine if
     it cites the report section).

Reject these outputs:
- "TODO: revisit later" / "follow-up: investigate"
- "Out of scope for this pass" without naming what *would* bring it in scope
- "Recommend the team consider X" — you are the team for this pass; act or
  justify.

If you cannot act *and* cannot justify (e.g., a real architectural decision is
needed), name the **specific blocker** in the report under a `## Escalations`
section: who/what would unblock it, and what the smallest concrete next action
is. Do not leave bare TODOs in code.
"""


CLEANUP_PROMPT = """\
You are performing a code quality cleanup pass on this repository. **Edits in
the tree come first** — remove dead code, consolidate duplicates, trim noise —
then the report summarizes what you changed; it is not a substitute for doing
the cleanup.

## Project Context
{project_context}

## Git Diff Summary (main...HEAD)
{diff_summary}

## Scope (act, don't flag)
1. **Dead code** — delete unused imports, commented-out blocks, stale
   experiments, removed-feature remnants.
2. **Comments** — remove outdated/wrong comments. Add a *short* `why` comment
   only where intent is non-obvious. Don't narrate obvious code.
3. **Consistency** — normalize naming, formatting, import order across files
   you touched. Match the surrounding style; don't impose a new one.
4. **File size** — files >500 LOC: extract a helper module *only* if there's
   a clean split. If not clean, list the file under "Files still >500 LOC" in
   the report with one of: (a) a concrete extraction plan for next pass, or
   (b) a justification for why this file legitimately needs that size.
5. **Duplicate utilities** — consolidate. Pick one canonical home, delete
   the duplicate, update callers.

## Rules
- Build and tests must still pass after this pass.
- No behavioral changes. No new features. No refactors that change call
  signatures of public API.

## Output (order matters)
1. **Working tree** — cleanup edits applied in-place per Scope and Rules.
2. **`docs/audits/cleanup-report.md`** — start with **## Changes made this
   pass**, then dead code removed, splits/consolidations, files still >500 LOC
   with plan or justification, and consistency edits (one line per file).

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace("{report_filename}", "cleanup-report.md")


DOCS_PROMPT = """\
You are performing a full documentation review and consolidation. Goal:
every doc statement is verifiable from current code; nothing else exists.

## Project Context
{project_context}

## Rules (non-negotiable)
- Markdown only. Docs only — no code refactors.
- Root holds **only** README.md (and untouched customer-voice files like
  BRAINDUMP.md / ROADMAP.md / vision docs).
- Everything else lives in `/docs`.
- BRAINDUMP.md is the customer's voice — never rewrite it.
- If you can't prove a statement from code/config/CI — don't document it.
- Wrong / outdated / duplicated / vague → fix or delete.
- No placeholder docs — every file earns its existence.

## Process
1. **Audit the repo**: entry points, schedulers, jobs, models, integrations,
   env vars, deployment, CI. Build the actual mental model.
2. **Inventory existing docs**: accuracy, necessity, location, duplication.
3. **Enforce structure**: README.md (root, lean: what / how-to-run / deploy
   basics / pointer to /docs); /docs holds the rest, named clearly.
4. **Rewrite or delete**: each doc you touch must reflect current reality.
   Strip references to non-existent files, removed flags, dead workflows.
5. **Validate**: every claim must be code-grounded.

## README.md must contain
- What the repo is (one paragraph)
- How to run it locally
- Deployment basics
- Pointer to /docs

## Output (order matters)
1. **Working tree** — README and `/docs` markdown updated or removed per Rules.
2. **`docs/audits/docs-consolidation.md`** — lists what you **changed** in those
   files (added/deleted/rewritten), statements removed as unverifiable, and any
   intentional gaps with reason — not a standalone essay that skips the edits.

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace("{report_filename}", "docs-consolidation.md")
