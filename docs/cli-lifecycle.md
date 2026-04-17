# CLI Lifecycle

## `aidlc run` Phase Order

`aidlc run` orchestrates a stateful run. Phase values persisted in state match `RunPhase` in **`aidlc.models`** (enum values are lowercase with underscores, e.g. `plan_finalization`).

Typical progression:

1. **`auditing`** (optional): only when `--audit` or `--audit full` is used
2. **`scanning`**: documentation and repo structure scan
3. **`planning`**: iterative issue/doc/research action cycles
4. **`plan_finalization`**: planning wind-down near budget end
5. **`implementing`**: issue-by-issue implementation
6. **`verifying`**: verification pass over implemented issues
7. **`validating`** (optional): test/fix loop
8. **`finalizing`** (optional): ssot/security/abend/docs/cleanup-style passes
9. **`reporting`** → **`done`**

There is also an initial **`init`** phase before the first substantive work in a new run.

## Run Modes

- **Default:** `aidlc run`
- **Plan-only:** `aidlc run --plan-only`
- **Implement-only:** `aidlc run --implement-only`
- **Resume latest:** `aidlc run --resume`
  - When the saved run is already past planning (`implementing` and later phases), resume **does not start a new planning cycle**. The scan step still runs to refresh context, then the prior phase is restored.
  - A short **resume reconcile** pass may mark issues as implemented when the issue id already appears in the git tree outside `.aidlc/` (best-effort; disable with `resume_reconcile_enabled: false` in config).
- **Dry run (no Claude execution):** `aidlc run --dry-run`
- **Audit before planning:** `aidlc run --audit` or `aidlc run --audit full`
- **Skip optional stages:** `--skip-validation`, `--skip-finalize` (not allowed in production profile)
- **Revert planning snapshot:** `--revert-to-cycle <n>`

## Precheck Behavior

- Precheck runs automatically before `run` except in `--resume` and `--implement-only`.
- `.aidlc/` and `.aidlc/config.json` are auto-created when missing.
- `--skip-precheck` is intentionally unsupported.
- Current required-doc set is empty; readiness scoring is based on recommended/optional docs.

## Planning Semantics

Planning can emit:

- `create_issue` / `update_issue`
- `create_doc` / `update_doc`
- `research`

Planner completion is controlled by cycle outcomes and guards:

- budget/cycle caps
- repeated no-new-issue cycles (diminishing returns)
- explicit `planning_complete` accepted only when completion is offered and core planning docs are sufficient
- consecutive-cycle failure ceiling (`max_consecutive_failures`)
- action-failure ratio threshold (`planning_action_failure_ratio_threshold`)

Core planning foundation currently means `ARCHITECTURE.md`, `DESIGN.md`, and `CLAUDE.md` meeting size/quality checks.

## Implementation and Verification

- issues are sorted by dependency and priority
- dependency cycles are treated as stop conditions
- implementation success requires structured JSON output
- tests are run when configured or auto-detected
- final verification marks implemented issues as verified and can fail/pause on test failures (`fail_on_final_test_failure`)
- optional strict git change verification can fail implementations (`strict_change_detection`)

## Validation Loop

When enabled, validator runs test tiers (`build`, `unit`, `integration`, `e2e`) and:

- parses failures
- creates fix issues
- re-implements fixes
- re-tests up to `validation_max_cycles`

Validation mode is SSOT-only:

- `test_profile_mode` must be `"progressive"`
- non-progressive modes are rejected at runtime

In strict settings, unstable validation pauses the run.

## Finalization

`finalize` pass order defaults to:

`ssot -> security -> abend -> docs -> cleanup`

During finalization, AIDLC also:

- refreshes config detections into `.aidlc/config.json`
- writes `AIDLC_FUTURES.md`

## Audit-to-Planning Handoff

In `full` audit mode, AIDLC can execute runtime checks (build/unit/integration/e2e), then generate
`BRAINDUMP.md` for planning handoff.

- Focus order: CI/build/test health -> coverage threshold -> Playwright/UAT depth.
- BRAINDUMP issue/research seeds are workload-capped against `plan_budget_hours` using
  `audit_planning_workload_stop_ratio`.

## Concurrency and State

- one active run per project via `.aidlc/run.lock`
- run state persists under `.aidlc/runs/<run_id>/state.json`
- checkpoint and report artifacts are written throughout the run
