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
- runtime execution checks (build/unit/integration/e2e) when commands are detected and `audit_runtime_enabled=true`
- planning handoff generation via `BRAINDUMP.md`

Full mode is bounded by:

- `audit_max_claude_calls`
- `audit_max_source_chars_per_module`
- `audit_runtime_timeout_seconds`

Standalone `aidlc audit --full` builds a `ProviderRouter` and requires `check_available()` to succeed (no configured provider / CLI available). User-facing messages may still mention Claude; quick audit does not require a provider CLI.

Runtime checks run with:

- `CI=1` environment default
- per-command timeout from `audit_runtime_timeout_seconds`
- Playwright headless normalization when `audit_playwright_headless=true`

## Outputs

Audit writes artifacts into the target repository:

- `STATUS.md` (always generated/updated)
- `ARCHITECTURE.md` (generated if missing)
- `.aidlc/audit_result.json`
- `.aidlc/CONFLICTS.md` when conflicts are detected
- `BRAINDUMP.md` (full audit, when `audit_braindump_enabled=true`)

## BRAINDUMP Workload Cap

`BRAINDUMP.md` is deliberately workload-capped for planning handoff.

- The cap uses `plan_budget_hours` and `audit_planning_workload_stop_ratio`.
- Audit continues gathering evidence, but stops adding active issue/research seeds once projected planning effort is near the cap.
- Overflow opportunities are listed in a deferred section when `audit_include_deferred_backlog=true`.

BRAINDUMP focus order is deterministic:

1. CI/build/test stabilization
2. coverage uplift toward `audit_coverage_threshold_percent`
3. Playwright/UAT depth and UX artifact review

## Conflict Semantics

Audit conflict detection compares generated understanding with existing docs. Typical conflicts include:

- documented project type mismatches
- references to missing modules
- major modules absent from current docs

When `aidlc run --audit ...` finds conflicts, the run pauses and sets a resume stop reason until conflicts are reviewed.

## Degraded Read Telemetry

Audit tracks degraded read counters (for example source/doc parse failures) and records them in `audit_result.json` as `degraded_stats`.
