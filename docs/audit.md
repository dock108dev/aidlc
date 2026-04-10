# Audit

AIDLC supports standalone auditing and pre-run auditing.

## Commands

- `aidlc audit` - quick audit
- `aidlc audit --full` - quick audit plus Claude-assisted full audit
- `aidlc run --audit` - run quick audit before planning
- `aidlc run --audit full` - run full audit before planning

## Quick Audit Behavior

Quick audit is local and deterministic. It includes:

- project type and framework detection
- module and entry-point discovery
- source stats and tech-debt marker detection
- heuristic test-coverage estimation

## Full Audit Behavior

Full audit adds Claude analysis on top of quick audit for:

- deeper module-level semantics
- richer feature inventory synthesis

Full mode is bounded by:

- `audit_max_claude_calls`
- `audit_max_source_chars_per_module`

Standalone `aidlc audit --full` exits if Claude CLI is unavailable.

## Outputs

Audit writes artifacts into the target repository:

- `STATUS.md` (always generated/updated)
- `ARCHITECTURE.md` (generated if missing)
- `.aidlc/audit_result.json`
- `.aidlc/CONFLICTS.md` when conflicts are detected

## Conflict Semantics

Audit conflict detection compares generated understanding with existing docs. Typical conflicts include:

- documented project type mismatches
- references to missing modules
- major modules absent from current docs

When `aidlc run --audit ...` finds conflicts, the run pauses and sets a resume stop reason until conflicts are reviewed.

## Degraded Read Telemetry

Audit tracks degraded read counters (for example source/doc parse failures) and records them in `audit_result.json` as `degraded_stats`.
