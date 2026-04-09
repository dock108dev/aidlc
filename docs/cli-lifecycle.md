# CLI Lifecycle

## Phase Flow

The `aidlc run` command executes:

1. `SCAN` - builds project context from documentation and structure
2. `PLAN` - iterates planning cycles until completion criteria or stop conditions
3. `IMPLEMENT` - executes issue implementation in dependency and priority order
4. `REPORT` - writes run report artifacts

## Run Modes

- **Default run**: `aidlc run`
- **Plan only**: `aidlc run --plan-only`
- **Implement only**: `aidlc run --implement-only`
- **Resume**: `aidlc run --resume`
- **Dry run**: `aidlc run --dry-run`
- **With audit first**: `aidlc run --audit` or `aidlc run --audit full`

## Precheck Rules

- Precheck runs before lifecycle by default.
- Precheck is skipped only for `--resume` and `--implement-only`.
- The `--skip-precheck` flag is not supported.

## Planning Behavior

Planning runs until one of these conditions:

- planning budget exhausted
- max planning cycle cap reached
- planning frontier clear (`no actions`)
- model explicitly declares planning complete
- diminishing returns condition (`diminishing_returns_threshold` cycles with zero new issues)
- too many consecutive planning failures

A planning cycle is treated as failed when:

- model call fails
- model output cannot be parsed as expected schema
- output validation fails
- action application fails

## Implementation Behavior

Implementation runs pending issues until resolved or blocked by stop conditions.

Hard-stop conditions include:

- dependency cycles in issue graph
- issues blocked by unmet dependencies

Result handling:

- structured JSON implementation result is required for success path
- unstructured fallback success is not used
- tests are run if configured or auto-detected
- final verification pass marks implemented issues as verified and optionally re-runs tests

## Concurrency Guard

Only one run is allowed per target project via `.aidlc/run.lock`.
