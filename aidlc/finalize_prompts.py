"""Prompt templates for AIDLC finalization passes.

Each constant is a full prompt template with {placeholders} for project-specific
context injection. The Finalizer class fills these before sending to the
provider.

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
    "ssot": "SSOT enforcement — diff-driven deletion of legacy / superseded code",
    "security": "Security audit — implement safe hardening, document the rest",
    "abend": "Error/warning suppression audit — tighten or document each catch",
    "cleanup": "Code cleanup — dead code, file size, consistency",
    "docs": "Documentation consolidation — rewrite docs to reflect current reality",
}


# Reusable trailer appended to every pass prompt — keeps the contract
# consistent and easy to audit/update in one place.
ACTIONABILITY_CONTRACT = """\
## Actionability Contract (non-negotiable)

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
main: code that contradicts what the branch is becoming gets deleted.

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

## Output
Make the deletions in-place. Write the destruction log to
`docs/audits/ssot-report.md` with:
- Diff-prioritized deletions (file/symbol, reason from diff, SSOT replacement)
- Final SSOT modules per domain
- Risk log: any legacy code intentionally retained, with diff-cited rationale
- Sanity check: no dangling references to deleted symbols

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace(
    "{report_filename}", "ssot-report.md"
)


SECURITY_PROMPT = """\
You are performing a deep security audit *and* applying safe hardening on
this branch's changes. Audit first, then fix what's safe inline; document the
rest with severity, evidence, and concrete remediation.

## Project Context
{project_context}

## Git Diff (main...HEAD)
{diff_summary}

## Phase 1 — Audit (focus on what changed)
Walk the diff and surrounding code. Map trust boundaries actually touched by
this branch. Look for, with evidence from the code:
- AuthN/Z gaps, missing server-side checks, IDOR-shaped routes
- Input handling: injection, traversal, deserialization, SSRF, unsafe regex
- Frontend: XSS sinks, unsafe HTML/markdown render, token leakage to client
- API/transport: missing validation, verbose errors, replay risk, CORS
- Secrets/config: hardcoded creds, env exposed to client bundle, debug flags
- Data exposure: PII in logs, oversharing in API responses, insecure caching
- Headers: CSP/HSTS/Frame-Options/cookie flags where applicable
- Abuse: rate limits, brute force, resource exhaustion, replay
- Suppressed errors hiding security-relevant failures

## Phase 2 — Apply safe hardening inline
Make these changes directly when low-risk and behavior-preserving:
- Tighten validation; add allow-lists; reject malformed input
- Redact secrets from logs and error messages
- Add missing security headers / cookie flags
- Narrow types/schemas where overly permissive
- Add `noindex` / `rel="noopener noreferrer"` where appropriate
- Remove accidental debug exposure

Do **not** make speculative breaking changes (auth model rewrites, big lib
upgrades, schema migrations). Those go in the report with a remediation plan.

## Output
Make safe hardening changes in-place. Write the audit to
`docs/audits/security-report.md`:
- Repo understanding (trust boundaries, sensitive surfaces)
- Findings table: title / severity / confidence / evidence / status
- Detailed findings per item with realistic exploit scenario + recommended fix
- "Safe hardening implemented this pass" — what you changed
- Remediation roadmap for the rest, prioritized by exposure

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace(
    "{report_filename}", "security-report.md"
)


ABEND_PROMPT = """\
You are auditing intentionally-handled / suppressed / downgraded errors,
warnings, logs, and guardrails. Goal: prove production posture is safe, or
tighten the cases that aren't.

## Project Context
{project_context}

## Git Diff (main...HEAD)
{diff_summary}

## Scope
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
3. **Tighten the bad ones now**: narrow `except Exception` to the actual
   class; remove `pass` after a real failure path; replace silent defaults
   with explicit raise; add the missing log; fix the wrong severity level.
4. **Justify the good ones now**: add a one-line comment at the suppression
   site citing the report section; keep `Note`-level cases as-is.

## Output
Make tightening changes in-place. Write the audit to
`docs/audits/error-handling-report.md`:
- Executive summary: counts by severity, top issues, posture verdict
- Findings table: ID / location / category / severity / disposition
- Per-finding details with code reference and rationale
- Categorization: acceptable-prod-notes / needs-doc / needs-telemetry /
  tighten-before-prod / hidden-failure-risk
- Final verdict: "Prod posture acceptable" or "Notable risk areas"

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace(
    "{report_filename}", "error-handling-report.md"
)


CLEANUP_PROMPT = """\
You are performing a code quality cleanup pass on this repository.

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

## Output
Make the cleanup changes in-place. Write the report to
`docs/audits/cleanup-report.md`:
- Dead code removed (file:line summaries)
- Files refactored / split
- Duplicates consolidated (chosen home + removed paths)
- Files still >500 LOC: each one with extraction plan OR justification
- Consistency changes made (one-line per file)

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace(
    "{report_filename}", "cleanup-report.md"
)


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

## Output
Rewrite docs in-place; delete obsolete docs. Write the change log to
`docs/audits/docs-consolidation.md`:
- Files added / deleted / consolidated
- Statements removed because unverifiable
- Intentional doc gaps left for future work (with reason)

Do not commit.
""" + ACTIONABILITY_CONTRACT.replace(
    "{report_filename}", "docs-consolidation.md"
)


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
