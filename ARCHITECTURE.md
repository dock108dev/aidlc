# Architecture

`aidlc` is a stateful CLI that runs an AI-assisted development life-cycle inside
a target repository. A single run progresses through phases — scan, plan,
implement, validate, finalize — emitting issues, code changes, and reports.
Every Claude/Copilot/OpenAI call is dispatched through one router so model and
account selection is centralized.

The product surface is intentionally narrow: the customer writes
`BRAINDUMP.md`, runs `aidlc run`, and the lifecycle does the rest. There are
no parallel "improve / plan / audit / finalize" entry points — those either
duplicated `run` or produced orthogonal artifacts and were removed in the
core-focus audit.

## High-level flow

```
aidlc init      (scaffolds .aidlc/ + BRAINDUMP.md)
aidlc run
   ├── scan         (ProjectScanner — read repo, detect type, find existing issues/docs)
   ├── audit        (optional, --audit; produces STATUS.md + audit_result.json — read-only)
   ├── plan         (Planner — repeated cycles → create_issue / create_doc / research)
   │   └── plan_finalization (wind-down near budget end)
   ├── implement    (Implementer — one issue at a time, dependency-sorted)
   ├── verify       (Final pass over implemented issues)
   ├── validate     (test/fix loop, optional)
   ├── finalize     (docs / cleanup passes)
   └── report
```

State for the run lives at `.aidlc/runs/<run_id>/state.json` and is checkpointed
on every cycle so the run is resumable.

## BRAINDUMP.md is the contract

`BRAINDUMP.md` is the single source of truth for what the user wants built. It
sits at the project root, is owned by the user, and is **never overwritten by
the tool**. The auditor used to generate a workload-capped BRAINDUMP from
runtime checks and overwrite the user's file; that inverted the design and was
removed. The auditor still runs (via `aidlc run --audit`) but only writes
`STATUS.md` (a generated artifact) and `.aidlc/audit_result.json`.

`aidlc init` scaffolds an empty `BRAINDUMP.md` template if missing. The user
fills it in, then runs `aidlc run`.

## Module map

| Module | Role |
|---|---|
| `aidlc/runner.py` | Orchestrates the full lifecycle; owns the run loop and phase transitions. |
| `aidlc/scanner.py` | Reads the project: docs, source files, prior `.aidlc/issues/`, audit cache. Builds the context blob the planner sees first. |
| `aidlc/planner.py` | Iterative planning loop — emits `create_issue`, `update_issue`, `create_doc`, `update_doc`, `research` actions. Decides when planning is done. |
| `aidlc/planner_helpers.py` | Prompt construction (`build_prompt`), prompt-budget enforcement, `_render_existing_issues_section`. The cache-friendly static prefix lives here. |
| `aidlc/implementer.py` | Drives implementation cycles, one issue per CLI call. Owns the early-stop logic (token exhaustion, dep cycles, consecutive failures). |
| `aidlc/implementer_helpers.py` | Implementation prompt builder. |
| `aidlc/implementer_signals.py` | Predicates over CLI results — `is_all_models_token_exhausted`, `should_stop_for_provider_availability`. |
| `aidlc/finalizer.py` | Runs finalization passes (`docs`, `cleanup`). |
| `aidlc/validator.py` | Test/fix loop after implementation. |
| `aidlc/auditor.py` + `aidlc/audit/` | Read-only code analysis. Triggered via `aidlc run --audit`; writes STATUS.md + audit_result.json. |
| `aidlc/doc_gap_detector.py` | TBD/placeholder scanner. **Opt-in** (`doc_gap_detection_enabled: true`); off by default to avoid spurious issues on mature repos. |
| `aidlc/state_manager.py` | `save_state` / `load_state`, `find_latest_run`. Run lock at `.aidlc/run.lock`. |
| `aidlc/config.py` | `DEFAULTS` dict, `_merge_user_config`, `load_config`, `write_default_config`. |
| `aidlc/routing/` | `ProviderRouter` — drop-in replacement for a single CLI; selects provider/account/model per call. |
| `aidlc/cli/`, `aidlc/cli_parser.py`, `aidlc/cli_commands.py` | argparse + per-subcommand handlers. |

## CLI surface

Core lifecycle:
- `aidlc init` — scaffold `.aidlc/` + `BRAINDUMP.md`
- `aidlc precheck` — readiness check
- `aidlc run` — full lifecycle
- `aidlc status` — last run summary
- `aidlc reset` — clear `.aidlc/` working state

Admin sugar:
- `aidlc accounts` — manage provider accounts
- `aidlc provider` — enable/disable/auth providers
- `aidlc usage` — token + cost reporting
- `aidlc config` — show/edit config

Removed in the core-focus audit (see `CHANGELOG.md`):
- `aidlc audit` — folded into `aidlc run --audit`
- `aidlc finalize` — now runs as part of `aidlc run`
- `aidlc improve` — duplicated `aidlc run`; concerns now go in `BRAINDUMP.md`
- `aidlc plan` — interactive multi-doc generator was orthogonal to the core flow
- `aidlc validate` — runs as part of `aidlc run`

## The router

`aidlc/routing/` resolves *one decision per call*: `RouteDecision(provider_id,
account_id, adapter, model, reasoning)`.

- `engine.py` — `ProviderRouter.execute_prompt()`: the entry point. Wraps a
  retry loop that handles rate limits, token exhaustion, and provider failures.
- `strategy_resolution.py` — strategies (`balanced`, `cheapest`, `best_quality`,
  `custom`) decide *which provider*.
- `context.py:resolve_model_for_phase` — given a chosen provider, picks the
  *model* by phase: `phase_models[phase]` → `default_model` → adapter default.
- `result_signals.py` — classifies CLI results: `is_token_exhaustion_result`,
  `is_rate_limited_result`, `is_model_exhausted_result` (per-model vs.
  per-provider exhaustion).

Two key invariants:

1. **Within-provider model fallback.** When a model returns `out of tokens`,
   the router consults the provider's `model_fallback_chain` (e.g.
   `["sonnet", "opus", "haiku"]`) and tries the next entry on the same
   provider before excluding the provider entirely. Only when the chain is
   exhausted does the loop move to the next enabled provider.
2. **User config wins over baked-in defaults.** A user setting
   `providers.claude.default_model: "opus"` in `.aidlc/config.json` overrides
   the DEFAULT `phase_models.<phase>` entries. Precedence is: user
   `phase_models[phase]` → user `default_model` → DEFAULT `phase_models[phase]`
   → DEFAULT `default_model` → adapter default. See `docs/configuration.md` for
   the full table.

## Planning prompt assembly

Planning prompts are built in `planner_helpers.py:build_prompt` with a
**cache-optimized** structure: a static prefix (instructions + JSON schema)
followed by a volatile body. Sections, in order, with their drop-priority
under prompt-budget pressure (`_enforce_prompt_budget`):

| Section | Drop priority |
|---|---|
| Instructions / schema (static prefix) | never |
| Run state (phase, cycle, elapsed/budget) | never |
| Doc-gap summary (when opt-in is enabled) | last |
| Foundation docs (BRAINDUMP / ROADMAP / ARCHITECTURE / DESIGN excerpts) | 3rd |
| Prior cycle notes | 2nd |
| Existing issues (current run + prior runs with status) | 1st (drop first) |

The "prior issues" and "foundation docs" sections are what stop a re-run
against an already-aidlc'd repo from re-planning from scratch. A planner with
no memory of prior decisions tends to rewrite working systems.

## Implementation prompt assembly

`implementer_helpers.py:build_implementation_prompt` produces a focused
prompt per issue containing:

- The full issue spec from `.aidlc/issues/<id>.md`
- Project context blob (from the scanner)
- Previous-attempt notes (`issue.implementation_notes`) on retries
- A `Must / Must not` block — including: "if a file/system already exists and
  works (has tests, has callers), modify in place; rewriting is a last resort".

The CLI returns structured JSON: `{issue_id, success, summary, files_changed,
tests_passed, notes, existing_callers_checked}`. The implementer parses this,
updates the issue status, and continues to the next.

## Finalization passes

`PASS_PROMPTS` is intentionally narrow: `docs`, `cleanup`. The legacy `ssot`,
`security`, and `abend` passes were removed because their semantics had
drifted (vague objectives, no clear definition of done) and the code shipped
prompts no one was confident in. New passes will be reintroduced once their
prompts and acceptance criteria are nailed down. See `aidlc/finalize_prompts.py`.

## Lifecycle of a run's working directory

```
<project_root>/
├── BRAINDUMP.md                      # customer's voice — never overwritten
├── STATUS.md                         # auto-generated by audit (optional)
└── .aidlc/
    ├── config.json                   # user + auth config (preserved by `aidlc reset`)
    ├── audit_result.json             # cached audit (deleted by reset)
    ├── planning_index.md             # docs/issues index for the planner
    ├── issues/                       # ISSUE-<N>.md files (deleted by reset unless --keep-issues)
    ├── runs/<run_id>/                # per-run: state.json, claude_outputs/, cycle_snapshots/
    └── reports/                      # per-run report markdown
```

`aidlc reset` clears everything except `config.json`. With `--all` it also
deletes `config.json` (after a confirmation prompt). With `--keep-issues` it
preserves `issues/`.

## Lifecycle and stop conditions

The planner and implementer each have explicit stop conditions.

**Planner** (`planner.py`):
- Budget exhausted → `plan_finalization` phase, then exit.
- `diminishing_returns_threshold` consecutive empty cycles → offer completion;
  if the model declines, force-exit one cycle later. Threshold is **adaptive**
  to issue count: `clamp(min, ceil(num_issues/10), max)`.
- Explicit `planning_complete` from the model (only honored after completion is
  offered).
- 3 consecutive failures.
- Action-failure-ratio above `planning_action_failure_ratio_threshold`.

**Implementer** (`implementer.py`):
- All issues resolved.
- All remaining issues blocked by unmet dependencies.
- Dependency cycle.
- 3 consecutive failures (re-sort and try; second cycle of failures exits).
- `should_stop_for_provider_availability(stop_reason)` → True (token
  exhaustion that survived the router's fallback chain, or no provider
  available).
- Max-cycle cap (typically only set in dry-run, default 0 = unlimited).

When the implementer stops with work remaining, finalization is **not**
auto-run. The user opts in via `implementation_finalize_on_early_stop: true`
(which now runs the `cleanup` pass only). The default is to log a clear
single-line stop reason and exit so budget is not spent on finalization at the
moment of failure.

## Status & abandoned-run handling

A run that exits cleanly leaves `state.status = complete` (or `failed`,
`paused`). A run killed externally (Ctrl-C, OOM, SIGTERM) used to leave
`status = running`, indistinguishable from a still-active run. The runner now
registers `atexit` + `SIGINT`/`SIGTERM` handlers that flip `status =
interrupted` on non-clean exit. On resume, any `running`/`interrupted` run
older than 1 hour is surfaced as `abandoned`, and the user is offered resume
or fresh-start.

## Test surface

Tests live under `tests/`. They cover:
- `tests/routing/` — strategy selection, fallback chain, rate-limit cooldown.
- `tests/planner/` — prompt construction, budget enforcement, prior-issues
  rendering.
- `tests/implementer/` — early-stop conditions, retry policy, JSON parsing.
- `tests/cli/` — argparse, `aidlc reset` flag combinations.
- `tests/integration/` — end-to-end with stubbed CLI.
