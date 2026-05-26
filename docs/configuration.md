# Configuration

AIDLC loads runtime configuration from the target repository, not from the
AIDLC checkout. The canonical defaults are the `DEFAULTS` dictionary in
`aidlc/config.py`.

## Load Order

`load_config()` applies configuration in this order:

1. Deep-copy `aidlc/config.py::DEFAULTS`.
2. Merge an explicit `--config` file, if provided, or `.aidlc/config.json` from
   the target repository.
3. Add internal paths: `_project_root`, `_aidlc_dir`, `_runs_dir`,
   `_reports_dir`, `_issues_dir`.
4. If `runtime_profile` is `production`, apply stricter defaults for keys the
   user did not explicitly set.

The `providers` block deep-merges by provider. `phase_models` also deep-merges,
so overriding one phase does not erase the others.

## Init Scaffold vs Effective Defaults

`aidlc init` writes a deliberately small `.aidlc/config.json`. It does not copy
every key from `DEFAULTS`; missing keys inherit at runtime. The packaged
`aidlc/configs/default.json` is a loadable config file, but it is not the init
scaffold and is not the canonical source of defaults.

## Core Runtime Defaults

| Key | Default | Notes |
|---|---:|---|
| `runtime_profile` | `standard` | `production` tightens validation/change gates. |
| `routing_strategy` | `balanced` | See provider routing below. |
| `plan_budget_hours` | `2` | Planning budget. |
| `checkpoint_interval_minutes` | `45` | Checkpoint cadence. |
| `dry_run` | `false` | Can also be set by `--dry-run`. |
| `max_planning_cycles` | `0` | `0` means unlimited; dry run is capped by runtime logic. |
| `max_implementation_cycles` | `0` | `0` means unlimited; dry run is capped by runtime logic. |
| `max_consecutive_failures` | `3` | Failure guard for planning/implementation loops. |

## Provider Routing

Default provider state:

- `openai` is enabled and uses the `codex` CLI.
- `claude` is disabled.
- `copilot` is disabled.

Important provider keys:

| Key | Meaning |
|---|---|
| `providers.<id>.enabled` | Disabled providers are skipped. |
| `providers.<id>.cli_command` | CLI binary or path. |
| `providers.<id>.max_capacity` | Marks a provider as preferred for implementation-heavy phases. |
| `providers.<id>.max_capacity_weight` | Weighted fairness input for balanced routing. |
| `providers.<id>.default_model` | Provider fallback model. |
| `providers.<id>.phase_models` | Per-phase model overrides. |
| `providers.<id>.model_fallback_chain` | Same-provider model retry order after token/quota exhaustion. |
| `providers.openai.model_reasoning_effort` | Passed to Codex/OpenAI CLI config as reasoning effort. |

Model selection precedence in `aidlc/routing/context.py`:

1. User-set `providers.<id>.phase_models[phase]`
2. User-set `providers.<id>.default_model`
3. Default `providers.<id>.phase_models[phase]`
4. Default `providers.<id>.default_model`
5. Adapter fallback

A user-set `default_model` intentionally beats default phase models. If you set
`providers.claude.default_model` to `opus`, it applies across phases unless you
also set phase-specific values.

## Rate Limits and Fallback

| Key | Default |
|---|---:|
| `routing_rate_limit_cooldown_seconds` | `300` |
| `routing_rate_limit_buffer_base_seconds` | `3600` |
| `stop_on_all_models_token_exhausted` | `true` |

When a model reports token exhaustion, the router tries the provider's
`model_fallback_chain` before excluding the provider. When every enabled
provider/model route is exhausted, the run stops cleanly so it can be resumed
after quota or billing recovers.

## Provider Execution Timeouts

Claude uses stream activity and terminal-event handling:

| Key | Default |
|---|---:|
| `claude_long_run_warn_seconds` | `300` |
| `claude_stall_warn_seconds` | `300` |
| `claude_stall_kill_seconds` | `0` |
| `claude_post_terminal_idle_seconds` | `30` |
| `claude_sigint_after_terminal_result` | `true` |
| `claude_timeout_grace_seconds` | `30` |

There is no Claude wall-clock hard timeout. `provider_call_timeout_seconds`
defaults to `1800` and applies to non-streaming provider CLIs such as Copilot
and Codex/OpenAI.

## Planning and Context

| Key | Default |
|---|---:|
| `finalization_budget_percent` | `10` |
| `planning_finalization_grace_cycles` | `1` |
| `max_doc_chars` | `10000` |
| `max_context_chars` | `40000` |
| `max_planning_prompt_chars` | `60000` |
| `planning_issue_index_max_items` | `15` |
| `planning_issue_index_include_all_until` | `12` |
| `planning_last_cycle_notes_max_chars` | `300` |
| `doc_gap_detection_enabled` | `false` |
| `doc_gap_max_items` | `50` |

`doc_scan_patterns` defaults to Markdown, text, and reStructuredText files.
`doc_scan_exclude` skips common generated/vendor directories including
`.git/`, `.aidlc/`, `node_modules/`, virtualenvs, `dist/`, and `build/`.

Doc-gap detection is opt-in. It scans docs for TBD-style markers and passes a
capped summary into planning when enabled.

## Implementation

| Key | Default |
|---|---:|
| `max_implementation_attempts` | `3` |
| `implementation_escalate_on_retry` | `true` |
| `implementation_reopen_verified_without_result` | `true` |
| `implementation_accept_pre_existing_suite_failures` | `true` |
| `implementation_pre_existing_debt_min_chars` | `40` |
| `implementation_pre_existing_prose_heuristic` | `true` |
| `implementation_use_targeted_tests_when_suite_unstable` | `true` |
| `implementation_targeted_test_sibling_expansion_cap` | `8` |
| `implementation_completed_issues_max` | `6` |
| `implementation_allowed_paths` | `null` |
| `strict_change_detection` | `false` |
| `fail_on_final_test_failure` | `false` |
| `implementation_finalize_on_early_stop` | `false` |

`implementation_targeted_test_command` can override targeted test construction.
It may use `{gtest_paths}` or `{paths}` placeholders.

## Validation

| Key | Default |
|---|---:|
| `validation_enabled` | `true` |
| `strict_validation` | `false` |
| `validation_allow_no_tests` | `true` |
| `fail_on_validation_incomplete` | `false` |
| `validation_max_cycles` | `3` |
| `validation_batch_size` | `10` |
| `test_profile_mode` | `progressive` |
| `run_tests_command` | `null` |
| `e2e_test_command` | `null` |
| `build_validation_command` | `null` |
| `test_timeout_seconds` | `300` |

`test_profile_mode` must be `progressive`; other values raise at validator
construction time. Missing test commands are skipped by default, but production
profile turns that into a failure unless the user explicitly overrides the
strict keys.

## Finalization

| Key | Default |
|---|---:|
| `finalize_enabled` | `true` |
| `finalize_passes` | `null` |
| `finalize_timeout_seconds` | `900` |
| `finalize_project_context_max_chars` | `22000` |
| `cleanup_passes_every_cycles` | `10` |
| `cleanup_passes_periodic` | `["cleanup"]` |

Available pass names are `cleanup` and `docs`. `finalize_passes: null` means
run all available passes in `PASS_ORDER` from `aidlc/finalize_prompts.py`.

Finalization can update `.aidlc/config.json` with newly detected commands after
code changes.

## Autosync

| Key | Default |
|---|---:|
| `autosync_enabled` | `true` |
| `autosync_every_implementation_cycles` | `25` |
| `autosync_finalize_before_push` | `true` |
| `autosync_push_remote` | `true` |
| `autosync_commit_message_template` | `aidlc: autosync after implementation cycle {cycle}` |
| `autosync_issue_status_sync` | `true` |
| `autosync_prune_enabled` | `true` |
| `autosync_runs_to_keep` | `5` |
| `autosync_keep_provider_outputs` | `200` |

Autosync uses git from the target repository. It is an implementation-phase
resilience feature, not CI/CD.

## Resume Reconcile

| Key | Default |
|---|---:|
| `resume_reconcile_enabled` | `false` |

When enabled, resume can mark pending/in-progress issues as implemented if
their issue ID appears in committed non-test source and several guard rails are
satisfied. It is off by default because false positives can skip real work.

## Production Profile

When `runtime_profile` is `production`, these defaults are applied only if the
user did not set them:

- `strict_validation: true`
- `validation_allow_no_tests: false`
- `fail_on_validation_incomplete: true`
- `fail_on_final_test_failure: true`
- `strict_change_detection: true`

`aidlc run` rejects `--skip-validation` and `--skip-finalize` in production
profile.

## Environment Variables

AIDLC does not use environment variables for core routing. Config and CLI flags
drive behavior.

| Variable | Used by |
|---|---|
| `EDITOR`, `VISUAL` | `aidlc config edit` |
| `CI` | Set to `1` inside audit runtime subprocesses |

Provider authentication uses each provider CLI's normal login mechanism.

## Unknown Keys

Unknown keys are loaded and retained in the runtime config dictionary, but they
only affect behavior if code reads them. Deprecated keys are documented in
[deprecations.md](deprecations.md).
