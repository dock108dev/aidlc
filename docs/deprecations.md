# Deprecations

This document lists behavior intentionally removed from the active CLI and
runtime. AIDLC favors one current path over retaining compatibility shims.

## Removed CLI Commands

| Removed command | Current path |
|---|---|
| `aidlc improve` | Put the concern in `BRAINDUMP.md` and run `aidlc run`. |
| `aidlc plan` | Use `aidlc init` to scaffold `BRAINDUMP.md`; planning is a phase of `aidlc run`. |
| `aidlc audit` | No CLI surface. `aidlc/auditor.py` remains as a Python API. |
| `aidlc finalize` | Finalization runs inside `aidlc run`; use `--skip-finalize` to skip. |
| `aidlc validate` | Validation runs inside `aidlc run`; use `--skip-validation` to skip. |

## Removed Planner Actions

Planning now emits only:

- `create_issue`
- `update_issue`

Removed action types:

- `create_doc`
- `update_doc`
- `research`

Discovery and research are standalone pre-planning phases, not planner actions.

## Removed Finalization Passes

Available finalization passes are `cleanup` and `docs`.

Removed passes:

- `ssot`
- `security`
- `abend`

Those pass names had unclear acceptance criteria and are not accepted by the
current finalizer.

## Removed Config Keys

| Removed key or family | Replacement or current behavior |
|---|---|
| `session_dir_max_keep` | No replacement; session-dir pruning belonged to the removed plan wizard. |
| `diminishing_returns_threshold` | No replacement; planning uses verify mode after no-new-issue cycles. |
| `diminishing_returns_window` | No replacement. |
| `planning_diminishing_returns_min_threshold` | No replacement. |
| `planning_diminishing_returns_max_threshold` | No replacement. |
| `audit_braindump_*` | No replacement; auditor no longer writes user-owned braindump docs. |
| `audit_planning_workload_*` | No replacement. |
| `audit_*_estimate_*` | No replacement. |
| `claude_hard_timeout_seconds` | Use activity-based `claude_stall_kill_seconds` if needed. |
| `autosync_keep_claude_outputs` | Use `autosync_keep_provider_outputs`. |

Unknown keys are loaded into config but only matter if current code reads them.

## Removed Phase and Usage Compatibility

- `RunPhase.AUDITING` is not a current phase.
- Legacy usage synthesis from old claude-only run state was removed; usage
  reporting now relies on provider/account telemetry in run state.

## Validation Compatibility

`test_profile_mode` must be `progressive`. Non-progressive validation modes are
not supported.

## Auditor Document Generation

The auditor no longer writes `BRAINDUMP.md` or `ARCHITECTURE.md`. `BRAINDUMP.md`
is user-owned input. The audit package may still write generated audit outputs
when used as a Python API, and `scanner.py` can consume `.aidlc/audit_result.json`
if an external process produced it.
