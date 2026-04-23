# Migration — existing aidlc'd projects

This guide covers what changes when you upgrade `aidlc` against a project
that already has `.aidlc/` populated from earlier runs. Most changes are
backward-compatible; two require user action.

## TL;DR

| You will notice | Why | What to do |
|---|---|---|
| `aidlc improve`, `aidlc plan`, `aidlc audit`, `aidlc finalize`, `aidlc validate` no longer exist | Core-focus audit | Audit + finalize run as part of `aidlc run`. To "improve" a specific concern, write it into `BRAINDUMP.md` and run `aidlc run`. |
| The auditor no longer writes `BRAINDUMP.md` or `ARCHITECTURE.md` | Core-focus audit | If your `BRAINDUMP.md` was previously auto-generated, replace it with what you actually want built. `aidlc init` scaffolds a starter template. |
| Finalization passes `ssot`, `security`, `abend` are gone | Core-focus audit | Their prompts had drifted. If you relied on them, expect smaller finalization output (just `docs` + `cleanup`). New passes will return once their prompts are nailed down. |
| Doc-gap detection is now off by default | Core-focus audit | Set `doc_gap_detection_enabled: true` to opt back in (useful on greenfield projects). |
| `providers.<id>.default_model` in your config now actually applies | ISSUE-003 fix | If you were working around the bug by setting `phase_models.<phase>` per-phase, you can simplify to a single `default_model` if that's what you wanted. |
| Single-provider runs no longer die at the first quota wall | ISSUE-004 fix | Optionally set `providers.<id>.model_fallback_chain: ["sonnet", "opus", "haiku"]` to control order. Default chain is sensible. |
| Planning no longer rewrites work from prior runs | ISSUE-005 / ISSUE-006 fix | Nothing — planner now sees prior issues and foundation docs as "already done". |
| Implementation no longer auto-runs finalization on early stop | ISSUE-009 default change | If you relied on this, set `implementation_finalize_on_early_stop: true` in `.aidlc/config.json`. The opt-in now runs `cleanup` only. |
| `aidlc reset` is now a real command | ISSUE-008 add | Use it instead of `rm -rf .aidlc/`. Preserves `config.json`. |
| Stale `status=running` runs from before this version may show as `abandoned` | ISSUE-010 detection | Resume them or wipe them. `aidlc status` shows a yellow ABANDONED badge. |

## What carries over

`aidlc reset` (default) preserves only `.aidlc/config.json`. Everything else
under `.aidlc/` is deleted: `runs/`, `reports/`, `issues/`, `session/`,
`audit_result.json`, `planning_index.md`, `CONFLICTS.md`, `run.lock`.

`aidlc reset --keep-issues` preserves `.aidlc/issues/`. Use this when you
want to reset run state but keep the planned issue backlog.

`aidlc reset --all` also deletes `config.json`, requiring re-init and
re-auth. Confirms before deletion.

## Recommended re-onboarding flow

For a project that ran prior aidlc versions and accumulated stale state:

```bash
# 1. Snapshot what's there before changing anything
cp -r .aidlc/ .aidlc-snapshot-pre-upgrade/

# 2. Inspect prior run status
aidlc status

# 3a. If you want a clean slate but keep config:
aidlc reset

# 3b. If you want to preserve issue backlog:
aidlc reset --keep-issues

# 4. Edit BRAINDUMP.md (or create one with `aidlc init`) — the customer's
#    voice is the entry point for the lifecycle. Prior issues, if kept, are
#    visible to the planner as "already done" context.

# 5. Verify model selection by setting your preferred model
#    in .aidlc/config.json:
#    {"providers": {"claude": {"default_model": "opus"}}}
#    Then check the router log:
aidlc run --dry-run --verbose | grep "Resolved model"
```

## Known migration edge cases

### Stub `ARCHITECTURE.md` from before ISSUE-002

If your project root has stub files like:

```
> ARCHITECTURE.md has been written to the project root. It covers all five
> requested sections grounded in the actual codebase:
> - Overview — Three.js + Cannon-es...
```

…that was the bug from the retired `aidlc plan` wizard. The actual content
was written by Claude under `.aidlc/session/<ts>/ARCHITECTURE.md`. To recover:

```bash
# Find the most recent session backup
ls -t .aidlc/session/

# Copy the real doc body back to root
cp .aidlc/session/<ts>/ARCHITECTURE.md .

# Repeat for ROADMAP.md, DESIGN.md, CLAUDE.md
```

Going forward there is no automated wizard for these docs — they are the
user's voice. Use `aidlc init --with-docs` to copy starter templates.

### Failed issues from the no-fallback era

Issues marked `failed` from runs that hit the single-model token wall (before
ISSUE-004) probably failed for a transient reason. Reopen them:

```bash
aidlc run --retry-failed
```

This reopens issues with `failure_cause in {token_exhausted, unknown}` —
those most likely to succeed on retry. Issues with cause `dependency` or
`test_regression` are left for manual review.

### `running` runs from a crashed session

If `aidlc status` shows a run as `running` but `last_updated` is hours old,
it crashed externally before this upgrade. The new code marks it
`abandoned` automatically on resume. You can also delete it:

```bash
rm -rf .aidlc/runs/<run_id>/
```

…or reset entirely.

### Diminishing-returns config

If you customized `diminishing_returns_threshold` in your config, it still
works but logs a deprecation. Migrate to:

```json
{
  "planning_diminishing_returns_min_threshold": 3,
  "planning_diminishing_returns_max_threshold": 6
}
```

The new keys give you a min/max range; the effective threshold scales with
issue count.
