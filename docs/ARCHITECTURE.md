# Architecture

AIDLC is a Python CLI that runs an AI-assisted development lifecycle inside a
target repository. It is not a server, daemon, scheduler, or background worker.
Each invocation runs from the command line, stores state under the target
repository's `.aidlc/` directory, and calls configured provider CLIs through a
shared router.

The current product contract is narrow:

1. The project owner writes `BRAINDUMP.md` at the target repository root.
2. `aidlc run` scans the repository, discovers/researches context, plans issues,
   implements them, validates, finalizes, and writes a report.
3. The generated `.aidlc/` state is resumable and resettable.

## Entry Points

| Surface | Code path | Notes |
|---|---|---|
| Console script | `pyproject.toml` -> `aidlc.__main__:main` | Installed command is `aidlc`. |
| Parser | `aidlc/cli_parser.py` | Defines all public subcommands and flags. |
| Command handlers | `aidlc/__main__.py`, `aidlc/cli_commands.py`, `aidlc/cli/` | Dispatches CLI actions. |
| Lifecycle runner | `aidlc/runner.py` | Orchestrates `aidlc run`. |

There are no production web routes, database migrations, cron jobs, or service
workers in this repository.

## Run Lifecycle

`aidlc run` persists phase values from `RunPhase` in `aidlc/models.py`:

1. `init` - initial state for a fresh run.
2. `scanning` - `ProjectScanner` reads docs, source signatures, existing issues,
   and optional `.aidlc/audit_result.json`.
3. `discovery` - one pre-planning provider call writes
   `.aidlc/discovery/findings.md` and `.aidlc/discovery/topics.json`.
4. `research` - one provider call per discovery topic writes
   `.aidlc/research/<slug>.md`.
5. `planning` - `Planner` emits `create_issue` and `update_issue` actions.
6. `plan_finalization` - planning wind-down near the budget boundary.
7. `implementing` - `Implementer` works issues in dependency order.
8. `verifying` - final verification pass over implemented issues.
9. `validating` - optional validation loop.
10. `finalizing` - optional `cleanup` and `docs` finalization passes.
11. `reporting` then `done` - report generation and terminal state.

Run state is saved in `.aidlc/runs/<run_id>/state.json`. Reports are written
under `.aidlc/reports/<run_id>/`.

## Core Modules

| Module | Responsibility |
|---|---|
| `aidlc/runner.py` | Lifecycle orchestration, resume behavior, auto-archive, validation/finalization/report dispatch. |
| `aidlc/scanner.py` | Project type detection, document/source scanning, prompt context construction. |
| `aidlc/discovery.py`, `aidlc/discovery_prompt.py` | Pre-planning discovery prompt and artifact writer. |
| `aidlc/research_phase.py`, `aidlc/research_output.py` | Discovery-topic research calls and markdown output parsing. |
| `aidlc/planner.py`, `aidlc/planner_*` | Planning loop, prompt assembly, action handling, dependency normalization. |
| `aidlc/implementer.py`, `aidlc/implementer_*` | Issue ordering, implementation prompts, retries, tests, autosync, targeted-test logic. |
| `aidlc/validator.py`, `aidlc/test_profiles.py`, `aidlc/test_parser.py` | Progressive validation commands, failure parsing, validation fix issue creation. |
| `aidlc/finalizer.py`, `aidlc/finalize_prompts.py` | `cleanup` and `docs` finalization prompts. |
| `aidlc/reporting.py`, `aidlc/run_outcome.py` | Run reports, checkpoint summaries, and terminal outcome classification. |
| `aidlc/state_manager.py` | State persistence, run locks, cycle snapshots, archive helpers, abandoned-run detection. |
| `aidlc/config.py`, `aidlc/config_detect.py` | Defaults, config loading, init config writing, project command detection. |

## Provider Routing

All provider execution goes through `ProviderRouter` in `aidlc/routing/engine.py`.
The router chooses a provider, account, and model per call.

| Area | Code path |
|---|---|
| Adapter construction | `aidlc/routing/adapter_registry.py` |
| Strategy selection | `aidlc/routing/strategy_resolution.py` |
| Model/account resolution | `aidlc/routing/context.py` |
| Cooldowns and rate-limit buffers | `aidlc/routing/cooldown.py` |
| Result classification | `aidlc/routing/result_signals.py` |
| Provider adapters | `aidlc/providers/claude_adapter.py`, `aidlc/providers/copilot_adapter.py`, `aidlc/providers/openai_adapter.py` |

Default config enables OpenAI/Codex and disables Claude/Copilot. Provider auth
is delegated to each vendor CLI; AIDLC does not define a single provider token
environment variable.

## State and Generated Artifacts

AIDLC writes generated working state into the target repository:

| Path | Owner | Purpose |
|---|---|---|
| `.aidlc/config.json` | User/AIDLC | Runtime config. Created by init/precheck if missing. |
| `.aidlc/issues/*.md` | AIDLC | Planned implementation issues. |
| `.aidlc/runs/<run_id>/state.json` | AIDLC | Resumable run state. |
| `.aidlc/runs/<run_id>/provider_outputs/` | AIDLC | Raw provider output saved during a run. |
| `.aidlc/reports/<run_id>/` | AIDLC | Run reports and checkpoints. |
| `.aidlc/discovery/`, `.aidlc/research/` | AIDLC | Pre-planning artifacts. |
| `.aidlc/_archive/` | AIDLC | Prior working state moved aside before fresh non-resume runs. |

The repository being worked on owns `BRAINDUMP.md`; AIDLC scaffolds it but does
not overwrite an existing one.

## Templates

`aidlc/project_template/BRAINDUMP.md` is the only template copied by
`aidlc init`. It is package data in `pyproject.toml`.

No other Markdown template is copied by the current init command.

## Audit Code

`aidlc/auditor.py` and `aidlc/audit/` remain in the codebase as Python APIs and
are covered by tests. There is no public `aidlc audit` CLI command. When an
external process produces `.aidlc/audit_result.json`, `scanner.py` can include
that result as planning context.
