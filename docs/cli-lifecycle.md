# CLI Lifecycle

This document describes the current CLI behavior implemented by
`aidlc/cli_parser.py`, `aidlc/__main__.py`, and `aidlc/runner.py`.

## Commands

| Command | Purpose |
|---|---|
| `aidlc init` | Create `.aidlc/config.json` and scaffold `BRAINDUMP.md` if missing. |
| `aidlc precheck` | Verify `BRAINDUMP.md` and `.aidlc/config.json`; auto-create config when missing. |
| `aidlc run` | Run the lifecycle. |
| `aidlc status` | Show the latest run status and issue breakdown. |
| `aidlc summarize-runs` | Summarize every `state.json` found under one or more paths. |
| `aidlc reset` | Delete generated `.aidlc/` working state. |
| `aidlc accounts` | Add, list, remove, and validate provider accounts. |
| `aidlc provider` | List, enable, disable, authenticate, or reconnect providers. |
| `aidlc usage` | Summarize provider usage by provider, phase, model, or account. |
| `aidlc config` | Show, edit, or interactively update `.aidlc/config.json`. |

Removed commands are listed in [deprecations.md](deprecations.md).

## Precheck and Init

`BRAINDUMP.md` at the target repository root is the only required user doc.
`aidlc precheck` and `aidlc run` treat a missing braindump as not ready.

`aidlc init` creates:

- `.aidlc/config.json`
- `.aidlc/issues/`
- `.aidlc/runs/`
- `.aidlc/reports/`
- `BRAINDUMP.md` from `aidlc/project_template/BRAINDUMP.md`, only if the file
  does not already exist

Init also appends ignore entries for `.aidlc/runs/`, `.aidlc/reports/`, and
`.aidlc/_archive/` when the target repository has no broad `.aidlc/` ignore.

## Fresh Runs, Resume, and Archive

A plain `aidlc run` starts from a clean AIDLC working state. Before the new run
is initialized, prior `.aidlc/issues/`, `.aidlc/runs/`, `.aidlc/reports/`,
`.aidlc/discovery/`, `.aidlc/research/`, and related run artifacts are moved
under `.aidlc/_archive/<timestamp>/`.

Archive is skipped when a flag needs prior state:

- `--resume`
- `--implement-only`
- `--retry-failed`
- `--reset-failed-attempts`

`aidlc run --resume` resumes the latest non-terminal run. If the latest run is
already terminal (`complete*`, `failed`, or `abandoned`), AIDLC starts a new run.
If a saved `running` or `interrupted` run is older than the abandonment
threshold, it is surfaced as `abandoned`.

## Run Modes and Flags

| Flag | Behavior |
|---|---|
| `--plan-only` | Stop after planning. |
| `--implement-only` | Skip planning and implement existing `.aidlc/issues/`. |
| `--resume` | Resume latest active run. |
| `--dry-run` | Avoid provider calls; cycle caps keep the smoke path bounded. |
| `--plan-budget <duration>` | Override planning budget, e.g. `30m` or `2h`. |
| `--max-plan-cycles N` | Override planning cycle cap; `0` means unlimited. |
| `--max-impl-cycles N` | Override implementation cycle cap; `0` means unlimited. |
| `--skip-validation` | Skip post-implementation validation; rejected in production profile. |
| `--skip-finalize` | Skip finalization; rejected in production profile. |
| `--passes docs,cleanup` | Select finalization passes. Valid pass names are `docs` and `cleanup`. |
| `--revert-to-cycle N` | Restore a planning cycle snapshot and exit. |
| `--retry-failed` | Reopen failed issues before implementation. |
| `--reset-failed-attempts` | Reset outage-marked failed attempts for retry. |

## Phase Order

`aidlc run` persists these phases in `state.json`:

1. `init`
2. `scanning`
3. `discovery`
4. `research`
5. `planning`
6. `plan_finalization`
7. `implementing`
8. `verifying`
9. `validating`
10. `finalizing`
11. `reporting`
12. `done`

On resume after planning, the scan step still runs to refresh context, then the
saved post-planning phase is restored instead of starting a new planning pass.

## Discovery and Research

Discovery runs one provider call and writes:

- `.aidlc/discovery/findings.md`
- `.aidlc/discovery/topics.json`

Research reads `topics.json` and writes one `.aidlc/research/<slug>.md` file
per topic. Existing discovery/research artifacts are reused unless they are
missing or known failure placeholders.

## Planning

Planning emits only the action types defined in `aidlc/schemas.py`:

- `create_issue`
- `update_issue`

The planner reads `BRAINDUMP.md`, discovery findings, research artifacts,
existing issues, and selected project docs. Dependency cleanup runs after each
planning cycle and removes invalid or cyclic issue edges.

Planning completion uses verify mode: after a cycle creates no new issues, the
next cycle prompts for coverage verification. If verify mode also finds no new
work, planning ends. If it creates issues, normal planning continues.

## Implementation and Verification

Implementation works issues in dependency order. Transient failed issues can be
reopened automatically, and `--retry-failed` can force reopening. Provider
continuation metadata is stored under the run directory so a retry or test-fix
prompt can continue the same provider thread for an issue attempt.

Verification runs after implementation and promotes implemented issues to
verified when the final checks pass.

## Validation

Validation is optional and enabled by default. It detects a progressive test
profile from the project type and config:

- build
- unit
- integration
- e2e

Progressive mode stops on the first failing tier. Validation records
`validation_status` as `passed`, `skipped`, `failed`, or `incomplete`. In
strict settings, missing tests or incomplete validation can pause the run.

## Finalization

Finalization is optional and enabled by default. Available passes are:

- `cleanup` - code cleanup prompt with diff context
- `docs` - markdown documentation prompt

Periodic cleanup can run during implementation based on
`cleanup_passes_every_cycles`; end-of-run finalization runs after validation.

## Terminal Outcomes

Current runs can finish with specific complete statuses:

| Status | Meaning |
|---|---|
| `complete_clean` | No unresolved issues, validation passed or was not required, and no recovered failures were recorded. |
| `complete_with_recovered_failures` | Work completed, but provider failures, issue retries, validation fixes, or earlier failed validation attempts occurred. |
| `complete_validation_skipped` | Work completed but validation was skipped by flag/config or had no configured tests. |
| `complete_validation_failed_allowed` | Validation failed or remained incomplete, but strict settings did not pause the run. |
| `complete_with_blocked_issues` | One or more issues remain pending, in progress, blocked, or failed. |
| `complete` | Legacy complete status still readable from older run state files. |

`aidlc summarize-runs` scans active and archived runs and prints compact
columns for outcome, issue count, validation, provider failures, time, and next
action.

## Reset

`aidlc reset` deletes generated AIDLC state from the target repository.

Default reset deletes:

- `.aidlc/runs/`
- `.aidlc/reports/`
- `.aidlc/issues/`
- `.aidlc/session/`
- `.aidlc/discovery/`
- `.aidlc/research/`
- `.aidlc/audit_result.json`
- `.aidlc/planning_index.md`
- `.aidlc/CONFLICTS.md`
- `.aidlc/run.lock`
- `.aidlc/_archive/`

It preserves `.aidlc/config.json` unless `--all` is used. `--keep-issues`
preserves `.aidlc/issues/`.
