"""Prompt templates for AIDLC finalization passes.

Each constant is a full prompt template with {placeholders} for project-specific
context injection. The Finalizer class fills these before sending to Claude.
"""

# Available passes in execution order
PASS_ORDER = ["ssot", "security", "abend", "docs", "cleanup"]

PASS_DESCRIPTIONS = {
    "ssot": "SSOT cleanup — remove dead code, enforce single source of truth",
    "security": "Security audit — identify vulnerabilities and hardening opportunities",
    "abend": "Error handling audit — catalog suppressed errors, fallbacks, retries",
    "docs": "Documentation consolidation — rewrite docs to reflect current reality",
    "cleanup": "Code cleanup — formatting, dead code, file size, consistency",
}


SSOT_PROMPT = """\
You are performing a destructive cleanup pass on this repository.

## Project Context
{project_context}

## Git Diff Summary (main...HEAD)
{diff_summary}

## Objective

Remove legacy, deprecated, and unused code paths. Enforce the current Single Source of Truth.

### Rules
1. SSOT always wins — code that duplicates or contradicts SSOT logic must be deleted
2. Backward compatibility is not a goal
3. Disabled or unreachable code must be removed (feature-flagged-off, env-gated, legacy fallbacks)
4. If prod usage cannot be proven, delete it

### Process
1. Review the diff above to identify removed/renamed flags, new SSOT modules, deprecated markers
2. Identify current authoritative sources for all features
3. Delete code that is: unreachable, superseded by new logic, gated by removed flags, exists only for backward compatibility
4. Replace silent fallback with hard failure where appropriate
5. Delete tests validating removed behavior
6. Update docstrings to reflect current behavior only

### Output
Write your findings and changes to `docs/audits/ssot-cleanup.md` with:
- Diff-Driven Deletion Summary (what was removed and why)
- SSOT Verification (final authoritative modules per domain)
- Risk Log (any intentionally retained legacy code)
- Sanity Check (no references to deleted symbols remain)

Make the actual code deletions in-place. Do not commit.
"""


SECURITY_PROMPT = """\
You are a senior application security engineer performing a deep security review.

## Project Context
{project_context}

## Objective

Perform a thorough security audit. Do not produce a generic checklist — inspect actual code.

### Review Areas
- Authentication and session security
- Authorization and access control (IDOR, role enforcement)
- Input handling and injection risks (SQL, command, template, path traversal)
- Frontend/browser security (XSS, unsafe rendering, token leakage)
- API and transport security (validation, error verbosity, CORS, rate limiting)
- Secrets, config, and environment handling
- Data protection and privacy risks
- Dependency and supply chain risks
- Logging and operational safety
- Abuse, misuse, and business logic risks

### For Each Finding
Provide: title, severity (critical/high/medium/low/informational), evidence from code,
realistic exploit scenario, and recommended fix.

Separate findings into:
1. Confirmed vulnerabilities
2. Risky patterns / hardening opportunities
3. Intentional or acceptable patterns worth documenting
4. Items needing manual verification

### Safe Direct Improvements
If there are obvious low-risk improvements (tightening validation, removing debug exposure,
adding safer defaults, redacting sensitive logs), implement them directly.

### Output
Write your full audit report to `docs/audits/security-audit.md`.
Make safe hardening changes in-place. Do not commit.
"""


ABEND_PROMPT = """\
You are auditing this codebase for intentionally handled, swallowed, downgraded, or suppressed errors.

## Project Context
{project_context}

## Objective

Produce a comprehensive audit of:
1. Things we catch intentionally
2. Things we suppress intentionally
3. Things we log and continue past
4. Things we downgrade from error to warning/info
5. Things we silently ignore or default around
6. Retries, fallbacks, circuit breakers, no-op behavior
7. Places where production behavior is intentionally quieter

### Search For
- try/except blocks (broad excepts, except Exception, bare except)
- Catches that pass, return None/[]/default, or log then continue
- Retry patterns, exponential backoff, fallback values
- Config defaulting, timeout handling, dry-run simulation
- Environment-specific strictness differences

### For Each Finding
Classify as: Note (acceptable), Low, Medium, High, or Critical.
Assess: reliability risk, data integrity risk, security risk, observability risk.

### Output
Write your full audit report to `docs/audits/abend-handling.md` with:
- Executive summary
- Detailed findings table
- Categorization (acceptable / needs telemetry / should tighten / high risk)
- Recommended remediation plan

Where you find dangerous silent failures, overly broad excepts, or missing observability,
fix them in-place. Tighten error handling, add logging where blind spots exist,
and convert silent failures to explicit error paths where appropriate.
Do not commit.
"""


DOCS_PROMPT = """\
You are performing a full documentation review and consolidation.

## Project Context
{project_context}

## Objective

Rewrite all documentation to reflect the current state of the codebase.

### Rules
- Documentation only — do not refactor code
- All docs must be Markdown (.md)
- Critical docs live in root (README.md only)
- Supporting docs live in /docs
- If docs are wrong, outdated, duplicated, or misleading — fix them
- If a doc provides no value — delete it
- If multiple docs overlap — consolidate

### Process
1. Audit the entire repo to understand what the system actually does today
2. Inventory all existing docs — are they accurate, needed, in the right place?
3. Enforce clean structure: README.md (root), everything else in /docs
4. Rewrite to reflect current reality. Remove references to non-existent files, old workflows, deprecated features
5. Validate every statement against actual code — if you can't prove it, don't document it
6. Ensure docs cover: setup, architecture, data models, integrations, configuration, deployment

### README.md Should Be
- What the repo is
- How to run it locally
- Deployment basics
- Where to find deeper docs (/docs)

### Output
Rewrite docs in-place. Delete obsolete docs. Write a summary of changes to
`docs/audits/docs-consolidation.md` explaining what was added, deleted, and consolidated.
Do not commit.
"""


CLEANUP_PROMPT = """\
You are performing a code quality cleanup pass on this repository.

## Project Context
{project_context}

## Objective

Bring the repo to a clean, consistent, maintainable state.

### Scope
1. **Dead code** — Remove unused imports, commented-out blocks, stale experiments
2. **Documentation in code** — Add short comments where intent is not obvious. Prefer "why" over "what". Remove outdated comments
3. **Consistency** — Normalize naming patterns, formatting, imports
4. **File size** — Flag files over 500 lines. Extract helpers where logical, but avoid over-splitting
5. **Duplicate utilities** — Consolidate shared logic

### Rules
- Repo must build and run cleanly after cleanup
- No behavioral changes
- No feature additions
- Linting and formatting should pass

### Output
Make cleanup changes in-place. Write a summary of what was cleaned to
`docs/audits/cleanup-report.md` including:
- Dead code removed
- Files refactored
- Files still over 500 LOC (with justification or flagged for follow-up)
- Consistency changes made

Do not commit.
"""


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

1. Review and update `ROADMAP.md` with next phase goals
2. Address any critical findings in the audit reports above
3. Run `aidlc audit` to refresh `STATUS.md` with current codebase state
4. Run `aidlc precheck` to verify all required docs are in place
5. Run `aidlc run` for the next development cycle

## Tips for a Productive Next Run

- **ROADMAP.md is king** — the planner creates issues from your roadmap phases
- **Be specific** — vague roadmap items produce vague issues
- **Acceptance criteria matter** — the implementer tests against them
- **Small phases** — break work into phases the planner can handle in one session
- **Review the plan first** — use `aidlc run --plan-only` to review before implementing

## Repo State

- **Branch**: {branch}
- **Date**: {date}
- **Project type**: {project_type}
"""
