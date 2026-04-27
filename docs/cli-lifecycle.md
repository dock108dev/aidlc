# CLI Lifecycle

## Surface

The CLI is intentionally narrow:

| Command | Purpose |
|---|---|
| `aidlc init` | Scaffold `.aidlc/` and `BRAINDUMP.md` (the customer's voice) |
| `aidlc precheck` | Verify `BRAINDUMP.md` and `.aidlc/` are in place |
| `aidlc run` | Run the full lifecycle |
| `aidlc status` | Show last run summary |
| `aidlc reset` | Clear stale `.aidlc/` working state |
| `aidlc accounts` / `provider` / `usage` / `config` | Admin |

`audit`, `finalize`, `improve`, `plan`, and `validate` were removed in the
core-focus audit:
- `finalize` runs inside `aidlc run` (use `--skip-finalize` to skip cleanup
  passes; pick a subset with `--passes`).
- `validate` runs inside `aidlc run` (use `--skip-validation` to skip).
- `improve` duplicated `run`; the way to focus a run on a concern is to
  write it into `BRAINDUMP.md`.
- `plan` was an orthogonal multi-doc generator; users now write
  `BRAINDUMP.md` and the planner phase consumes it.
- `audit` had no equivalent integration into `aidlc run`. The auditor module
  (`aidlc/auditor.py`) remains as an internal Python API but has no current
  CLI surface — see [deprecations.md](deprecations.md).

## `aidlc run` Phase Order

`aidlc run` orchestrates a stateful run. Phase values persisted in state match
`RunPhase` in `aidlc.models` (enum values are lowercase with underscores,
e.g. `plan_finalization`).

Typical progression:

1. **`init`** — initial phase before any substantive work in a fresh run
2. **`scanning`** — repo + documentation scan via `ProjectScanner`
3. **`discovery`** — single pre-planning model pass; reads `BRAINDUMP.md`
   and the repo, writes `.aidlc/discovery/findings.md` + `.aidlc/discovery/topics.json`.
   **Idempotent**: skipped on resume if both artifacts already exist.
4. **`research`** — one model call per discovery-nominated topic; writes
   `.aidlc/research/<slug>.md` per entry. **Skip-if-exists per topic**.
5. **`planning`** — iterative `create_issue` / `update_issue` action cycles
6. **`plan_finalization`** — planning wind-down near budget end
7. **`implementing`** — issue-by-issue implementation
8. **`verifying`** — verification pass over implemented issues
9. **`validating`** (optional) — test/fix loop
10. **`finalizing`** (optional) — `docs` and `cleanup` passes
11. **`reporting`** → **`done`**

## Run Modes

- **Default:** `aidlc run`
- **Plan-only:** `aidlc run --plan-only`
- **Implement-only:** `aidlc run --implement-only`
- **Resume latest:** `aidlc run --resume`
  - When the saved run is already past planning (`implementing` and later
    phases), resume **does not start a new planning cycle**. The scan step
    still runs to refresh context, then the prior phase is restored.
  - A short **resume reconcile** pass may mark issues as implemented when
    three guard rails are all met: status is `pending`/`in_progress`,
    `attempt_count == 0` (so issues actively worked on this run are left
    alone), AND the issue id appears in at least one **non-test** source
    file in the git tree. Disable with `resume_reconcile_enabled: false`
    in config. See `docs/configuration.md` for details.
  - If the latest run shows `status=running` or `interrupted` and
    `last_updated` is older than 1 hour, it is surfaced as `abandoned` and
    you are prompted to resume or start fresh.
- **Retry transient failures:** `aidlc run --retry-failed` reopens issues
  whose `failure_cause` is `failed_token_exhausted` or `failed_unknown`
  before resuming. Issues with cause `failed_dependency` or
  `failed_test_regression` are left for manual review.
- **Dry run (no provider execution):** `aidlc run --dry-run`
- **Skip optional stages:** `--skip-validation`, `--skip-finalize` (not
  allowed in production profile)
- **Pick finalization passes:** `--passes docs` or `--passes docs,cleanup`
- **Revert planning snapshot:** `--revert-to-cycle <n>`

## `aidlc reset`

Clears stale run state without nuking your config.

- Default: deletes `.aidlc/runs/`, `reports/`, `issues/`, `session/`,
  `discovery/`, `research/`, `audit_result.json`, `planning_index.md`,
  `CONFLICTS.md`, `run.lock`. **Preserves** `.aidlc/config.json`.
- `--all`: also deletes `config.json` (requires re-init and re-auth).
- `--keep-issues`: preserves `.aidlc/issues/` for cases where you want to
  reset run state but keep the planned backlog.
- `--dry-run`: prints what would be deleted; deletes nothing.
- `--yes` / `-y`: skips the confirmation prompt.

Use this instead of `rm -rf .aidlc/`.

## Precheck Behavior

- Precheck runs automatically before `run` except in `--resume` and
  `--implement-only`.
- `BRAINDUMP.md` at the project root is **required**. Without it, the run
  exits early — see `aidlc/precheck.py`.
- `.aidlc/` and `.aidlc/config.json` are auto-created when missing.
- `--skip-precheck` is intentionally unsupported.

## Discovery and Research

Discovery and research are pre-planning model passes that write
**tool-generated artifacts** under `.aidlc/` (not under the target
repo's `docs/` tree — they are AIDLC working state, not user-authored
documentation):

- **Discovery** (`aidlc/discovery.py`): one model call. Reads `BRAINDUMP.md`
  + a repo summary. Writes:
  - `.aidlc/discovery/findings.md` — markdown findings
  - `.aidlc/discovery/topics.json` — JSON list of `{topic, question, scope}`
    entries the research phase should investigate
- **Research** (`aidlc/research_phase.py`): one model call per topic in
  `topics.json`. Writes `.aidlc/research/<slug>.md`. Per-topic failures log a
  warning but do not fail the run; the next topic continues.

Both phases are **idempotent**: existing artifacts are not regenerated unless
deleted. This is what makes resume cheap.

The planner reads the discovery findings and lists `.aidlc/research/*.md`
filenames in its prompt so it can reference researched answers without
re-deriving them.

## Planning Semantics

The planner emits two action types (defined in `PLANNING_ACTION_TYPES`):

- `create_issue`
- `update_issue`

Planner completion is controlled by cycle outcomes and guards:

- budget/cycle caps
- a no-new-issue cycle (no actions, or only `update_issue` actions)
  triggers **verify mode** (one-shot) for the next cycle. Verify swaps
  the normal prompt for an explicit coverage-check prompt that walks
  through BRAINDUMP, discovery findings, research files, and the
  existing issue set. If verify also returns no new issues, planning
  completes. If verify surfaces missing work, the planner files those
  issues and returns to normal mode — the next empty cycle then ends
  planning directly without re-verifying (verify is one-shot per run)
- explicit `planning_complete` accepted only when completion is offered and
  core planning docs are sufficient
- consecutive-cycle failure ceiling (`max_consecutive_failures`)
- action-failure ratio threshold (`planning_action_failure_ratio_threshold`)

The planner's prompt also includes:

- **Discovery findings + research file index** when present (always retained
  under prompt-budget pressure).
- **Prior Run — Already Done (do not redo)**: a section listing prior
  `.aidlc/issues/` with status (verified / implemented / failed / pending)
  and a one-line implementation-notes excerpt.
- **Foundation Docs (committed — incremental changes only)**: the first
  ~2 KB of each of `ROADMAP.md`, `ARCHITECTURE.md`, `DESIGN.md` if present
  at the **target repo root**. Other optional context refs the planner reads
  if present: `README.md`, `STATUS.md`, `CLAUDE.md`.

The "prior issues" and "foundation docs" sections are dropped first under
prompt-budget pressure (so the schema/instructions remain intact).

### Dependency-graph normalization

Each planning cycle ends with `_sanitize_issue_dependencies()`, which:

- drops non-string / empty / self / unknown / duplicate dependency edges
- detects cycles
- breaks each cycle by removing one edge (heuristic: pick the
  lower-priority / heavier-dependency source)

A `logger.warning` is emitted for every change. Issue markdown is rewritten
to reflect the cleaned graph. See `aidlc/planner_dependency_graph.py`.

### Doc-gap detection (opt-in)

Set `doc_gap_detection_enabled: true` in `.aidlc/config.json` to let the
scanner surface TBD/placeholder markers as planning input. **Off by
default** because it created spurious issues on mature repos.

## Implementation and Verification

- issues are sorted by dependency and priority via
  `aidlc/implementer_issue_order.py:sort_issues_for_implementation`, which
  topologically orders and **automatically breaks any remaining cycles**
  (one edge per cycle, with a `logger.warning`).
- implementation success normally comes from the model's structured JSON
  output, which includes the optional `existing_callers_checked: [<file:line>, …]`
  field. **Fallback:** when the model wrote files via tools but the JSON
  envelope is missing or garbled (mid-output timeout, trailing prose,
  duplicated JSON blocks), the implementer trusts the git diff and
  proceeds — `files_changed` is populated from `git diff` and the test
  step decides success. Throwing the work away and retrying the entire
  issue would cost ~$5 per attempt for no functional benefit; the test
  step is the real gate.
- tests are run when configured or auto-detected.
- final verification marks implemented issues as verified and can fail/pause
  on test failures (`fail_on_final_test_failure`).
- optional strict git change verification can fail implementations
  (`strict_change_detection`).

### Early stop and resume

When implementation stops with work remaining (token exhaustion that survived
the router's fallback chain, dependency cycle, consecutive failures), the
implementer logs a single visually-distinct stop-reason line and a
`RESUME WITH:` instruction, then exits.

### Interrupted-attempt recovery

If a run is killed mid-attempt (Ctrl-C, SIGTERM, OOM, hard timeout), the
issue's state is persisted with `status=in_progress` and `attempt_count`
already incremented (the increment happens at the START of an attempt).
On resume, the implementer detects this and **restarts the same attempt**
rather than incrementing the counter again — one killed attempt should
not consume two of `max_attempts`.

The resume warning includes a note that the working tree may contain
partial changes from the killed attempt; the model receives whatever the
tree currently shows and decides whether to extend or revert. AIDLC does
not snapshot/restore the working tree itself — that stays under your git
control.

By default, finalization **does not** auto-run on early stop — the prior
behavior burned more budget at exactly the moment we wanted to stop cleanly.
To opt back in, set `implementation_finalize_on_early_stop: true` in
`.aidlc/config.json`; that runs the `cleanup` pass.

Failed issues record a `failure_cause`: `failed_token_exhausted`,
`failed_dependency`, `failed_test_regression`, or `failed_unknown`. On the
next implementation cycle, transient causes (`failed_token_exhausted`,
`failed_unknown`) are auto-reopened to `pending`. Use `--retry-failed` to
force-reopen all causes.

### Periodic cleanup

When `cleanup_passes_every_cycles > 0` (default 10) and `finalize_enabled`
is true, the implementer runs the configured `cleanup_passes_periodic`
subset (default `["abend", "cleanup"]`) every N cycles. This runs the
finalizer mid-implementation to keep code health high. See
`aidlc/implementer_finalize.py`.

## Validation Loop

When enabled, validator runs test tiers (`build`, `unit`, `integration`,
`e2e`) and:

- parses failures
- creates fix issues
- re-implements fixes
- re-tests up to `validation_max_cycles`

Validation mode is SSOT-only:

- `test_profile_mode` must be `"progressive"`
- non-progressive modes raise at construction time

In strict settings, unstable validation pauses the run.

## Finalization

`finalize_passes` defaults to `null` (run all available passes). Available
passes: `docs`, `cleanup`. The legacy `ssot`, `security`, `abend` passes
were removed in the core-focus audit because their semantics had drifted;
new passes will be reintroduced once their prompts and acceptance criteria
are nailed down.

During finalization, AIDLC also:

- refreshes config detections into `.aidlc/config.json`
- writes `AIDLC_FUTURES.md`

## Concurrency and State

- one active run per project via `.aidlc/run.lock`
- run state persists under `.aidlc/runs/<run_id>/state.json`
- checkpoint and report artifacts are written throughout the run
- `atexit` + `SIGINT`/`SIGTERM` handlers flip `state.status` from `running`
  → `interrupted` on non-clean exit
- on resume, any `running`/`interrupted` run with `last_updated` older than
  1 hour is surfaced as `abandoned` (yellow ABANDONED badge in
  `aidlc status`); user is offered resume or fresh-start
