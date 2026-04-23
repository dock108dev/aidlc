# Audit

The auditor analyzes an existing codebase and produces read-only artifacts.
It is run as part of `aidlc run --audit` (the standalone `aidlc audit`
subcommand was removed in the core-focus audit).

## Read-only invariant

The auditor **never** writes user-owned docs. It used to generate or
overwrite `BRAINDUMP.md` and `ARCHITECTURE.md` based on heuristics; that
inverted the design where `BRAINDUMP.md` is the customer's voice. Now the
auditor only writes:

- `STATUS.md` (a generated artifact at project root)
- `.aidlc/audit_result.json` (machine-readable summary)
- `.aidlc/CONFLICTS.md` (when conflicts with existing docs are detected)

If your project doesn't have an `ARCHITECTURE.md`, the audit will not
scaffold one — that doc is your voice to write. Use
`aidlc init --with-docs` to copy a template you can fill in.

## Commands

- `aidlc run --audit` — run quick audit before planning
- `aidlc run --audit full` — run full audit before planning

## Quick Audit Behavior

Quick audit is local and deterministic. It includes:

- project type and framework detection
- module and entry-point discovery
- source stats and tech-debt marker detection
- heuristic test-coverage estimation

## Full Audit Behavior

Full audit adds provider-assisted analysis on top of quick audit for:

- deeper module-level semantics
- richer feature inventory synthesis
- runtime execution checks (build/unit/integration/e2e) when commands are
  detected and `audit_runtime_enabled=true`

Full mode is bounded by:

- `audit_max_claude_calls`
- `audit_max_source_chars_per_module`
- `audit_runtime_timeout_seconds`

Full mode requires a configured provider CLI (router `check_available()`
must succeed). Quick mode does not.

Runtime checks run with:

- `CI=1` environment default
- per-command timeout from `audit_runtime_timeout_seconds`
- Playwright headless normalization when `audit_playwright_headless=true`

## Conflict Semantics

Audit conflict detection compares generated understanding with existing
docs. Typical conflicts include:

- documented project type mismatches
- references to missing modules
- major modules absent from current docs

When `aidlc run --audit ...` finds conflicts, the run pauses and sets a
resume stop reason until conflicts are reviewed in `.aidlc/CONFLICTS.md`.

## Degraded Read Telemetry

Audit tracks degraded read counters (for example source/doc parse failures)
and records them in `audit_result.json` as `degraded_stats`.
