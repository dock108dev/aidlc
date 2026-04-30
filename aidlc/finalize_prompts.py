"""Prompt templates for AIDLC finalization passes.

Each constant is a full prompt template with {placeholders} for project-specific
context injection. The Finalizer class fills these before sending to the
provider.

Code-oriented passes (ssot, security, abend, cleanup) assume **edit permission
is for changing the product**: implement fixes and removals in the tree first;
the markdown under ``docs/audits/`` records *what you changed* and any
intentional non-fixes — it must not read like a standalone audit you could have
written without touching files.

Every pass enforces the **Actionability Contract**: each finding must either be
fixed in-place during the pass OR documented as an intentional non-fix with
concrete justification, both in the pass report (``docs/audits/<pass>.md``)
and at the relevant code location (docstring or inline comment). Bare TODO /
follow-up notes are rejected — pick a side.
"""

# Available passes in canonical end-of-run order. Periodic cleanup runs a
# subset (see config: cleanup_passes_periodic).
PASS_ORDER = ["ssot", "security", "abend", "cleanup", "docs"]

PASS_DESCRIPTIONS = {
    "ssot": "SSOT enforcement — diff-driven deletion of legacy / superseded code in-repo",
    "security": "Security hardening — fix safe issues in code; report summarizes edits + residual risk",
    "abend": "Error-handling hardening — tighten catches/suppressions in code; report logs dispositions",
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


SSOT_PROMPT = """\
You are performing an SSOT (Single Source of Truth) enforcement pass. This is
a *destructive* cleanup driven by the diff between the current branch and
main: code that contradicts what the branch is becoming gets **deleted from
the repo** — the report is a log of what you removed or intentionally kept, not
the main artifact.

## Project Context
{project_context}

## Git Diff (main...HEAD)
{diff_summary}

## Non-negotiables
- SSOT always wins. Anything that duplicates or contradicts it is deleted.
- Backward compatibility is not a goal. Old flags/configs/callers go.
- Diff > speculation. Prioritize what the diff *proves* is obsolete.
- Disabled, unreachable, or feature-flagged-off code is dead — remove it.
- If production usage cannot be proven, default to removal.

## Process
1. **Diff scan** — extract: removed/renamed flags, new SSOT modules, code
   moved to a new home, "temporary/legacy/deprecated" markers, paths bypassed
   by new guards, tasks/endpoints no longer referenced.
2. **Identify SSOTs** — for each domain touched by the branch, name the
   single authoritative module. Anything outside it is suspect.
3. **Inventory candidates** — duplicates, legacy fallbacks, removed-flag
   readers, compatibility shims, dead tests of removed behavior.
4. **Delete** — actually remove the files / functions / branches. Replace
   silent fallbacks with `raise RuntimeError("Legacy path removed — use
   <ssot_module>")` if reachability is genuinely uncertain.
5. **Tests** — delete tests for removed behavior; add a negative test that
   fails if a deprecated symbol is reintroduced (when feasible).
6. **Docs** — strip references to deleted flags/modes from existing docs.

## Output (order matters)
1. **Working tree** — deletions and replacements applied in-place (see Process).
2. **`docs/audits/ssot-report.md`** — start with **## Changes made this pass**
   (symbols/files removed or rewritten, SSOT replacement). Then final SSOT
   modules per domain, risk log for anything intentionally retained, and a
   sanity check for dangling references.

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace("{report_filename}", "ssot-report.md")


SECURITY_PROMPT = """\
You are performing **security hardening** on this branch. The point of this
pass is **edits in the product**: validation, redaction, headers, narrowing
unsafe patterns, removing accidental exposure — merged into the repo. Walking
the code to find issues is necessary, but **a report without matching code
changes (where fixes are safe and in-scope) is a failed pass**.

## Project Context
{project_context}

## Git Diff (main...HEAD)
{diff_summary}

## Non-negotiables
- **Implement first.** When a fix is low-risk and behavior-preserving, apply it
  in the file immediately; do not defer to prose “recommendations” you could
  execute yourself.
- Use the diff to prioritize where to look; widen to call sites and trust
  boundaries the branch touches.
- Do **not** make speculative breaking changes (auth model rewrites, large
  dependency upgrades, schema migrations). Those belong in the report with
  evidence and the smallest concrete follow-up — after you have applied every
  safe inline fix you reasonably can.

## What to inspect (evidence-backed; fix as you go when safe)
Walk the diff and surrounding code. Map trust boundaries this branch touches.
For each class of issue below, **prefer a patch** over a writeup when the patch
is small and preserves intended behavior:
- AuthN/Z gaps, missing server-side checks, IDOR-shaped routes
- Input handling: injection, traversal, deserialization, SSRF, unsafe regex
- Frontend: XSS sinks, unsafe HTML/markdown render, token leakage to client
- API/transport: missing validation, verbose errors, replay risk, CORS
- Secrets/config: hardcoded creds, env exposed to client bundle, debug flags
- Data exposure: PII in logs, oversharing in API responses, insecure caching
- Headers: CSP/HSTS/Frame-Options/cookie flags where applicable
- Abuse: rate limits, brute force, resource exhaustion, replay
- Suppressed errors hiding security-relevant failures

Typical **safe hardening** edits (examples — do what the codebase needs):
- Tighten validation; add allow-lists; reject malformed input
- Redact secrets from logs and error messages
- Add missing security headers / cookie flags
- Narrow types/schemas where overly permissive
- Add `noindex` / `rel="noopener noreferrer"` where appropriate
- Remove accidental debug exposure

## Output (order matters)
1. **Working tree** — all safe hardening edits applied in-place; build/tests
   still pass if the repo defines them.
2. **`docs/audits/security-report.md`** — start with **## Changes made this
   pass** (bullet list: `path` + one line per edit). Then trust boundaries /
   sensitive surfaces briefly, then a **findings** section only for items you
   did **not** fix here (severity / confidence / evidence / smallest next step).
   Do not bury the actual code work under a generic audit narrative.

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace("{report_filename}", "security-report.md")


ABEND_PROMPT = """\
You are **hardening** intentionally-handled / suppressed / downgraded errors,
warnings, logs, and guardrails. Goal: **change the code** so production posture
is safer — narrower catches, real logging, explicit failures instead of silent
swallows — not to produce a standalone “audit” that leaves behavior unchanged
where a tight fix was obvious.

## Project Context
{project_context}

## Git Diff (main...HEAD)
{diff_summary}

## Scope (inventory → then edit)
Find and judge every instance of:
- `try` / `except` (bare, broad, log-and-continue, silent return)
- Catches that convert errors to warnings/info or to falsey defaults
- Suppression comments: `noqa`, `type: ignore`, `pylint: disable`, etc.
- `warnings.filterwarnings`, lint disables, deprecation muting
- Retries, fallbacks, "best effort" / "non-fatal" / "expected failure" notes
- Env-gated strictness changes (debug-only assertions, prod-only suppression)
- Background jobs/webhooks that ack-and-drop failures
- Validation that warns but accepts

## Risk lenses (apply to every finding)
- Reliability — does this hide real failures?
- Data integrity — can this corrupt/lose data silently?
- Security — does this hide auth/permission/audit issues?
- Observability — does this make incidents harder to diagnose?
- Operational — silent degradation, pager noise, hard-to-trace bugs?

## Severity
- **Critical** — likely hides serious prod failures / security / data loss
- **High** — meaningful prod risk, fix soon
- **Medium** — acceptable for now; tighten when convenient
- **Low** — minor blind spot
- **Note** — intentional, low-risk; documented and done

## Process
1. Inventory every suppression / catch / fallback in the diff and surrounding
   code (focus on changed files first).
2. For each: classify the risk lens, severity, confidence.
3. **Tighten the bad ones now** (in source): narrow `except Exception` to the
   actual class; remove `pass` after a real failure path; replace silent
   defaults with explicit raise; add the missing log; fix the wrong severity
   level.
4. **Justify the good ones now** (in source): add a one-line comment at the
   suppression site citing the report section; keep `Note`-level cases as-is.

## Output (order matters)
1. **Working tree** — tightening and justification edits merged; build/tests
   still pass if configured.
2. **`docs/audits/error-handling-report.md`** — lead with **## Changes made
   this pass** (paths + what you tightened or annotated). Then executive
   summary (counts by severity, posture verdict), findings table for anything
   that still needs follow-up, and per-item rationale. The report reflects the
   code you changed; it does not replace those edits.

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace("{report_filename}", "error-handling-report.md")


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


FUTURES_TEMPLATE = """\
# AIDLC Futures — Preparing for Next Round

*Auto-generated by AIDLC finalization on {date}*

## What Was Done This Run

- **Run ID**: {run_id}
- **Issues planned**: {total_issues}
- **Issues implemented**: {issues_implemented}
- **Issues verified**: {issues_verified}
- **Issues failed**: {issues_failed}
- **Finalization passes completed**: {passes_completed}

## Audit Reports

{audit_report_links}

## Preparing for Next Run

1. Update `BRAINDUMP.md` with what you want next — that is the single source of truth
2. Review and update supporting docs (`README.md`, `ARCHITECTURE.md`, `DESIGN.md`, optional `ROADMAP.md`)
3. Address any critical findings in the audit reports above
4. Run `aidlc run` for the next development cycle

## Tips for a Productive Next Run

- **BRAINDUMP.md is the contract** — be specific about what you want
- **Acceptance criteria matter** — the implementer tests against them
- **Optional roadmap** — use `ROADMAP.md` for milestone tracking if your team prefers phases
- **Review the plan first** — use `aidlc run --plan-only` to review before implementing

## Repo State

- **Branch**: {branch}
- **Date**: {date}
- **Project type**: {project_type}
"""
