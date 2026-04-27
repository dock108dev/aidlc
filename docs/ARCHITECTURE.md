# Architecture

`aidlc` is a stateful CLI that runs an AI-assisted development life-cycle inside
a target repository. A single run progresses through phases — scan, discover,
research, plan, implement, validate, finalize — emitting issues, code changes,
and reports. Every Claude/Copilot/OpenAI call is dispatched through one router
so model and account selection is centralized.

The product surface is intentionally narrow: the customer writes
`BRAINDUMP.md`, runs `aidlc run`, and the lifecycle does the rest. There are
no parallel "improve / plan / audit / finalize" entry points — those either
duplicated `run` or produced orthogonal artifacts and were removed in the
core-focus audit.

## High-level flow

```
aidlc init      (scaffolds .aidlc/ + BRAINDUMP.md template)
aidlc run
   ├── scan                (ProjectScanner — read repo, detect type, find existing issues/docs)
   ├── discovery           (single pre-planning model pass — writes .aidlc/discovery/findings.md + topics.json)
   ├── research            (one model call per discovery-nominated topic — writes .aidlc/research/<slug>.md)
   ├── plan                (Planner — repeated cycles emit create_issue / update_issue actions)
   │   └── plan_finalization (wind-down near budget end)
   ├── implement           (Implementer — one issue at a time, dependency-sorted)
   ├── verify              (Final pass over implemented issues)
   ├── validate            (test/fix loop, optional, --skip-validation to skip)
   ├── finalize            (docs / cleanup passes, optional, --skip-finalize to skip)
   └── report
```

State for the run lives at `.aidlc/runs/<run_id>/state.json` and is
checkpointed on every cycle so the run is resumable. The run phases tracked in
state are defined by the `RunPhase` enum in `aidlc/models.py`:
`init, scanning, discovery, research, planning, plan_finalization,
implementing, verifying, validating, finalizing, reporting, done`.

## BRAINDUMP.md is the contract

`BRAINDUMP.md` is the single source of truth for what the user wants built. It
sits at the project root, is owned by the user, and is **never overwritten by
the tool**. `aidlc/precheck.py` requires it before any run; without it,
`aidlc run` exits early.

`aidlc init` scaffolds an empty template if missing. The user fills it in,
then runs `aidlc run`.

## Module map

### Core lifecycle

| Module | Role |
|---|---|
| `aidlc/runner.py` | Orchestrates the full lifecycle; owns the run loop and phase transitions. |
| `aidlc/scanner.py` | Reads the project: docs, source files, prior `.aidlc/issues/`, audit cache. Builds the context blob the planner sees first. |
| `aidlc/discovery.py` + `aidlc/discovery_prompt.py` | Single pre-planning model pass: writes `.aidlc/discovery/findings.md` + `.aidlc/discovery/topics.json`. |
| `aidlc/research_phase.py` + `aidlc/research_output.py` | Executes the discovery-nominated topic list, writing `.aidlc/research/<slug>.md` per entry. |
| `aidlc/planner.py` | Iterative planning loop — emits `create_issue` / `update_issue` actions. Decides when planning is done. |
| `aidlc/planner_dependency_graph.py` | Pure dependency-graph normalization: edge scrubbing + cycle detection + automatic cycle breaking (one edge per pass). |
| `aidlc/planner_helpers.py` | Prompt construction (`build_prompt`), prompt-budget enforcement, cache-friendly static prefix. |
| `aidlc/planner_text.py` | Static instruction blocks for the planning prompts. |
| `aidlc/implementer.py` | Drives implementation cycles, one issue per CLI call. Owns the early-stop logic (token exhaustion, dep cycles, consecutive failures). |
| `aidlc/implementer_helpers.py` | Implementation prompt builder, test detection, transient-failure reopen. |
| `aidlc/implementer_workspace.py` | Git probes (`git_has_changes`, `git_current_branch`), autosync commit/push, run-cache pruning. |
| `aidlc/implementer_signals.py` | Predicates over CLI results — `is_all_models_token_exhausted`, `should_stop_for_provider_availability`. |
| `aidlc/implementer_issue_order.py` | Topological issue ordering with automatic cycle breaking. |
| `aidlc/implementer_targeted_tests.py` | Targeted-test command construction when the project-wide suite is unstable. |
| `aidlc/implementer_finalize.py` | Periodic-cleanup + pre-push finalize orchestration mid-run. |
| `aidlc/finalizer.py` + `aidlc/finalize_prompts.py` | Runs finalization passes (`docs`, `cleanup`). |
| `aidlc/validator.py` | Test/fix loop after implementation. |
| `aidlc/doc_gap_detector.py` | TBD/placeholder scanner. **Opt-in** (`doc_gap_detection_enabled: true`); off by default. |

### CLI infrastructure

| Module | Role |
|---|---|
| `aidlc/__main__.py`, `aidlc/cli_parser.py`, `aidlc/cli_commands.py` | argparse + per-subcommand handlers. |
| `aidlc/cli/` | Per-command modules: `accounts`, `provider`, `usage_cmd`, `config_cmd`, `display`. |

### Routing

| Module | Role |
|---|---|
| `aidlc/routing/engine.py` | `ProviderRouter` — drop-in replacement for a single CLI; selects provider/account/model per call; runs the resolve+execute loop. |
| `aidlc/routing/strategy_resolution.py` | Strategies (`balanced`, `cheapest`, `best_quality`, `custom`) decide *which provider*. |
| `aidlc/routing/context.py` | Provider ordering, account selection, model resolution, `fallback_decision`. |
| `aidlc/routing/cooldown.py` | Per-provider/per-model cooldown bookkeeping + exponential rate-limit buffer. |
| `aidlc/routing/result_signals.py` | Classifies CLI results: `is_token_exhaustion_result`, `is_rate_limited_result`, `is_model_exhausted_result`. |
| `aidlc/routing/adapter_registry.py` | Builds `{provider_id → adapter}` from config. |
| `aidlc/routing/helpers.py` | Phase classification (`get_quality_sensitive_phases`, `implementation_phases`, `get_budget_providers`). |
| `aidlc/routing/types.py` | `RouteDecision`, `RoutingStrategy`, `UsagePressure` data shapes. |

### Provider adapters

| Module | Role |
|---|---|
| `aidlc/providers/base.py` | Common adapter interface + heartbeat-aware subprocess wait. |
| `aidlc/providers/claude_adapter.py` | Wraps `aidlc/claude_cli.py`. |
| `aidlc/providers/copilot_adapter.py`, `aidlc/providers/openai_adapter.py` | Other CLI integrations. |
| `aidlc/claude_cli.py` | Subprocess + retry/outage budget for the `claude` CLI. |
| `aidlc/claude_cli_metadata.py` | Parses Claude CLI stream-json output into `(text, usage, cost, model_used, source)`. |

### Accounts

| Module | Role |
|---|---|
| `aidlc/accounts/manager.py` | `AccountManager`: register/list/validate provider accounts. |
| `aidlc/accounts/credentials.py` | Keyring-first credential store with plaintext fallback (`~/.aidlc/credentials.json`). |
| `aidlc/accounts/models.py` | `Account`, `AuthState`, `MembershipTier` data shapes. |

### State, config, audit

| Module | Role |
|---|---|
| `aidlc/state_manager.py` | `save_state` / `load_state`, `find_latest_run`, run lock at `.aidlc/run.lock`. |
| `aidlc/config.py` | `DEFAULTS` dict, `_merge_user_config`, `load_config`, `write_default_config`. |
| `aidlc/config_detect.py` | Auto-detect test/lint commands from project markers. |
| `aidlc/auditor.py` + `aidlc/audit/` | Read-only code analysis as a Python API. **No CLI surface today** — see [deprecations.md](deprecations.md). When `.aidlc/audit_result.json` is present (e.g. produced externally), `scanner.py` consumes it as planner context. |

## CLI surface

Core lifecycle:
- `aidlc init` — scaffold `.aidlc/` + `BRAINDUMP.md`
- `aidlc precheck` — readiness check
- `aidlc run` — full lifecycle
- `aidlc status` — last run summary
- `aidlc reset` — clear `.aidlc/` working state

Admin:
- `aidlc accounts` — manage provider accounts
- `aidlc provider` — enable/disable/auth providers
- `aidlc usage` — token + cost reporting
- `aidlc config` — show/edit config

Removed in the core-focus audit (see [deprecations.md](deprecations.md)):
`aidlc audit`, `aidlc finalize`, `aidlc improve`, `aidlc plan`, `aidlc validate`.

## The router

`aidlc/routing/` resolves *one decision per call*:
`RouteDecision(provider_id, account_id, adapter, model, reasoning, ...)`.

- `engine.py` — `ProviderRouter.execute_prompt()`: the entry point. Wraps a
  retry loop that handles rate limits, token exhaustion, and provider
  failures. Cooldown bookkeeping lives in `cooldown.py`.
- `strategy_resolution.py` — strategies (`balanced`, `cheapest`, `best_quality`,
  `custom`) decide *which provider*.
- `context.py:resolve_model_for_phase` — given a chosen provider, picks the
  *model* by phase: user `phase_models[phase]` → user `default_model` →
  DEFAULT `phase_models[phase]` → DEFAULT `default_model` → adapter default.
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
   the DEFAULT `phase_models.<phase>` entries. See
   [configuration.md](configuration.md) for the full precedence table.

## Planning prompt assembly

Planning prompts are built in `planner_helpers.py:build_prompt` with a
**cache-optimized** structure: a static prefix (instructions + JSON schema)
followed by a volatile body. Sections, in order, with their drop-priority
under prompt-budget pressure (`_enforce_prompt_budget`):

| Section | Drop priority |
|---|---|
| Instructions / schema (static prefix) | never |
| Run state (phase, cycle, elapsed/budget) | never |
| Discovery findings + research file index (when present) | never |
| Doc-gap summary (when opt-in is enabled) | last |
| Foundation docs (BRAINDUMP / ROADMAP / ARCHITECTURE / DESIGN excerpts at the **target repo root**) | 3rd |
| Prior cycle notes | 2nd |
| Existing issues (current run + prior runs with status) | 1st (drop first) |

The "prior issues" and "foundation docs" sections are what stop a re-run
against an already-aidlc'd repo from re-planning from scratch.

## Planning action types

The planner is constrained to two action types (defined in
`aidlc/schemas.py:PLANNING_ACTION_TYPES`):

| Action | Effect |
|---|---|
| `create_issue` | Create a new issue for implementation |
| `update_issue` | Refine an existing issue |

The historical `create_doc` / `update_doc` / `research` action types were
removed; discovery and research are now standalone phases that run before
planning, not planning actions.

## Implementation prompt assembly

`implementer_helpers.py:build_implementation_prompt` produces a focused
prompt per issue containing:

- The full issue spec from `.aidlc/issues/<id>.md`
- Project context blob (from the scanner)
- Previous-attempt notes (`issue.implementation_notes`) on retries
- A `Must / Must not` block — including: "if a file/system already exists and
  works (has tests, has callers), modify in place; rewriting is a last resort"
- A list of `.aidlc/research/*.md` filenames so the implementer reads relevant
  research before designing a change

The CLI returns structured JSON: `{issue_id, success, summary, files_changed,
tests_passed, notes, existing_callers_checked}`. The implementer parses this,
updates the issue status, and continues to the next.

## Finalization passes

`PASS_PROMPTS` is intentionally narrow: `docs`, `cleanup`. The legacy `ssot`,
`security`, and `abend` passes were removed because their semantics had
drifted (vague objectives, no clear definition of done). New passes will be
reintroduced once their prompts and acceptance criteria are nailed down. See
`aidlc/finalize_prompts.py`.

## Lifecycle of a run's working directory

```
<project_root>/
├── BRAINDUMP.md                      # customer's voice — never overwritten
└── .aidlc/
    ├── config.json                   # user + auth config (preserved by `aidlc reset`)
    ├── discovery/                    # generated by discovery phase
    │   ├── findings.md
    │   └── topics.json
    ├── research/                     # one <slug>.md per discovery-nominated topic
    ├── audit_result.json             # consumed if present; not produced by `aidlc run`
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
- A no-new-issue cycle triggers **verify mode** (one-shot) for the next
  cycle. Verify uses an explicit coverage-check prompt (BRAINDUMP +
  findings + research + existing issues). If verify also returns 0 new
  issues, planning completes. If verify surfaces gaps, those issues
  are filed and normal cycles resume; verify will not fire again this
  run, so the next empty cycle ends planning directly.
- Explicit `planning_complete` from the model (only honored after completion is
  offered).
- 3 consecutive failures.
- Action-failure-ratio above `planning_action_failure_ratio_threshold`.

**Implementer** (`implementer.py`):
- All issues resolved.
- All remaining issues blocked by unmet dependencies.
- Dependency cycle that survives auto-break (rare — both planner and
  implementer-issue-ordering remove cycle edges automatically).
- 3 consecutive failures (re-sort and try; second cycle of failures exits).
- `should_stop_for_provider_availability(stop_reason)` → True (token
  exhaustion that survived the router's fallback chain, or no provider
  available).
- Max-cycle cap (typically only set in dry-run; default 0 = unlimited).

When the implementer stops with work remaining, finalization is **not**
auto-run. The user opts in via `implementation_finalize_on_early_stop: true`
(which now runs the `cleanup` pass only). The default is to log a clear
single-line stop reason and exit so budget is not spent on finalization at the
moment of failure.

## Status & abandoned-run handling

A run that exits cleanly leaves `state.status = complete` (or `failed`,
`paused`). The runner registers `atexit` + `SIGINT`/`SIGTERM` handlers that
flip `status = interrupted` on non-clean exit. On resume, any
`running`/`interrupted` run older than 1 hour is surfaced as `abandoned`, and
the user is offered resume or fresh-start.

## Test surface

Tests live as a flat directory under `tests/` (no subpackages). Areas covered
(by file prefix):

- `test_routing_*`, `test_strategy_*`, `test_context_*` —
  routing, strategy selection, cooldown.
- `test_planner*` — prompt construction, budget enforcement,
  dependency-graph normalization (covered by `test_planner.py`'s
  `_sanitize_issue_dependencies` tests).
- `test_implementer*`, `test_implementer_extended.py` — early-stop conditions,
  retry policy, JSON parsing, issue ordering.
- `test_cli_*` — argparse, `aidlc reset` flag combinations, accounts/provider
  subcommands.
- `test_discovery*`, `test_research_phase.py` — pre-planning phases.
- `test_audit_*`, `test_auditor.py` — read-only auditor (Python API).
- `test_validation.py` — test/fix loop.

CI runs `make lint` (ruff check + format check) and the full pytest suite
with coverage.
