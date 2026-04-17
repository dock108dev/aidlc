# Configuration

Configuration is loaded from `.aidlc/config.json` in the target repository, unless `--config` is provided.

Canonical defaults live in `aidlc/config.py`.

## Resolution Rules

1. start with built-in defaults
2. merge user config from `.aidlc/config.json` (or explicit config file)
3. set internal runtime paths (`_project_root`, `_aidlc_dir`, `_runs_dir`, `_reports_dir`, `_issues_dir`)
4. if `runtime_profile == "production"`, apply strict profile defaults for keys not explicitly set by the user

## Defaults by Area

### Core Runtime

| Key | Default |
|---|---|
| `runtime_profile` | `"standard"` |
| `plan_budget_hours` | `4` |
| `checkpoint_interval_minutes` | `15` |
| `dry_run` | `false` |
| `max_consecutive_failures` | `3` |
| `max_planning_cycles` | `0` (unlimited) |
| `max_implementation_cycles` | `0` (unlimited) |

### Provider routing

| Key | Default |
|---|---|
| `routing_strategy` | `"balanced"` |
| `routing_rate_limit_cooldown_seconds` | `300` |
| `routing_rate_limit_buffer_base_seconds` | `3600` (1h) |

On HTTP 429 / rate-limit responses, the router waits until the provider-reported reset time **plus** a buffer: `base × 1`, `base × 2`, `base × 4` … capped at **`base × 8`** hours per consecutive rate limit on the same provider/model (resets after a successful call). Set `routing_rate_limit_buffer_base_seconds` to `0` to disable the extra wait when no time is reported (tests / debugging).

**Per-provider routing** (under `providers.<id>` in the same JSON):

| Key | Default | Meaning |
|---|---|---|
| `max_capacity` | `true` for `claude`, otherwise `false` | Mark a backend as **high token capacity** vs Copilot/OpenAI-style backends. |
| `max_capacity_weight` | `20` when `max_capacity` is true, else `1` | On **planning, research, audit**, etc. (not implementation), balanced mode rotates by **weighted fairness**: lower `calls ÷ weight` is preferred first, so a weight-20 provider receives roughly 20× the first-choice share vs a weight-1 peer over time. |

For **`implementation`** and **`implementation_complex`**, every provider with `max_capacity: true` is ordered **before** other providers (stable order: claude → copilot → openai among those enabled). Model IDs per phase are still driven by `phase_models` — this only chooses **which CLI** runs first.

### Claude Execution

| Key | Default |
|---|---|
| `claude_cli_command` | `"claude"` |
| `claude_model` | `"opus"` |
| `claude_model_planning` | `"sonnet"` |
| `claude_model_research` | `"sonnet"` |
| `claude_model_implementation` | `"sonnet"` |
| `claude_model_implementation_complex` | `"opus"` |
| `claude_model_finalization` | `"sonnet"` |
| `claude_long_run_warn_seconds` | `300` |
| `claude_hard_timeout_seconds` | `1800` (30 minutes) |
| `claude_timeout_grace_seconds` | `30` |
| `telemetry_cost_mode` | `"auto"` |
| `telemetry_estimate_usd` | `false` |
| `telemetry_model_pricing_usd_per_million_tokens` | `{"default": {"input": 3.0, "output": 15.0, "cache_creation_input": 3.75, "cache_read_input": 0.30}, "sonnet": {...}, "opus": {...}, "haiku": {...}}` |
| `retry_max_attempts` | `2` |
| `retry_base_delay_seconds` | `30` |
| `retry_max_delay_seconds` | `300` |
| `retry_backoff_factor` | `2.0` |
| `claude_service_outage_max_wait_seconds` | `7200` (2 hours) |

`telemetry_cost_mode` values:

- `auto`: use exact CLI cost metadata when available, estimate otherwise (only if `telemetry_estimate_usd` is true)
- `exact_only`: track only exact cost metadata (no fallback estimates)
- `estimate_only`: always estimate using pricing table and token counts (ignores `telemetry_estimate_usd`)

`telemetry_estimate_usd`: when `false` (default), token counts are still recorded but **no** USD is computed from `telemetry_model_pricing_*` in `auto` mode (API list $/M is not a subscription bill). Set `true` if you want rough API-reference dollar estimates.

### Planning and Context

| Key | Default |
|---|---|
| `diminishing_returns_window` | `5` |
| `diminishing_returns_threshold` | `2` |
| `finalization_budget_percent` | `10` |
| `planning_doc_min_chars` | `800` |
| `planning_action_failure_ratio_threshold` | `0.6` |
| `max_doc_chars` | `10000` |
| `max_context_chars` | `80000` |
| `max_implementation_context_chars` | `30000` |
| `project_brief_max_chars` | `20000` |
| `phase_context_max_chars` | `20000` |
| `max_planning_prompt_chars` | `60000` |
| `planning_issue_index_max_items` | `40` |
| `planning_issue_index_include_all_until` | `30` |
| `planning_last_cycle_notes_max_chars` | `500` |
| `doc_scan_patterns` | `["**/*.md", "**/*.txt", "**/*.rst"]` |
| `doc_scan_exclude` | `["node_modules/**", ".git/**", "venv/**", ".venv/**", "__pycache__/**", ".aidlc/**", "dist/**", "build/**"]` |
| `doc_gap_detection_enabled` | `true` |
| `doc_gap_max_items` | `50` |

### Implementation and Testing

| Key | Default |
|---|---|
| `max_implementation_attempts` | `3` |
| `implementation_escalate_on_retry` | `true` |
| `implementation_accept_pre_existing_suite_failures` | `true` |
| `implementation_pre_existing_debt_min_chars` | `40` |
| `implementation_complexity_acceptance_criteria_threshold` | `12` |
| `implementation_complexity_dependencies_threshold` | `5` |
| `implementation_complexity_description_chars_threshold` | `5000` |
| `implementation_complexity_labels` | `["architecture", "security", "migration", "refactor-core", "cross-cutting"]` |
| `run_tests_command` | `null` |
| `test_timeout_seconds` | `300` |
| `implementation_allowed_paths` | `null` |
| `strict_change_detection` | `false` |
| `fail_on_final_test_failure` | `false` |

After implementation, if `run_tests_command` fails, AIDLC runs a **fix-tests** prompt. If tests still fail, but the model returns structured JSON documenting **pre-existing / unrelated** suite failures (`failures_are_pre_existing_unrelated` + `follow_up_documentation`), the issue can still be marked **implemented** when `implementation_accept_pre_existing_suite_failures` is `true` and the documentation is at least `implementation_pre_existing_debt_min_chars` long — notes are appended for follow-up issues. Set `implementation_accept_pre_existing_suite_failures` to `false` to require a green test command for every issue.

### Validation Loop

| Key | Default |
|---|---|
| `validation_enabled` | `true` |
| `strict_validation` | `false` |
| `validation_allow_no_tests` | `true` |
| `fail_on_validation_incomplete` | `false` |
| `validation_max_cycles` | `3` |
| `validation_batch_size` | `10` |
| `test_profile_mode` | `"progressive"` (only supported mode) |
| `e2e_test_command` | `null` |
| `build_validation_command` | `null` |

### Audit

| Key | Default |
|---|---|
| `audit_depth` | `"quick"` |
| `audit_max_claude_calls` | `10` |
| `audit_max_source_chars_per_module` | `15000` |
| `audit_source_extensions` | `[".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb"]` |
| `audit_exclude_patterns` | `["**/test*/**", "**/vendor/**", "**/node_modules/**", "**/.git/**"]` |
| `audit_runtime_enabled` | `true` |
| `audit_runtime_timeout_seconds` | `600` |
| `audit_coverage_threshold_percent` | `85` |
| `audit_playwright_headless` | `true` |
| `audit_playwright_command_override` | `null` |
| `audit_braindump_enabled` | `true` |
| `audit_braindump_path` | `"BRAINDUMP.md"` |
| `audit_planning_workload_stop_ratio` | `0.95` |
| `audit_research_estimate_default_hours` | `2.0` |
| `audit_issue_estimate_defaults` | `{"high": 3.0, "medium": 1.5, "low": 0.75}` |
| `audit_include_deferred_backlog` | `true` |

### Research

| Key | Default |
|---|---|
| `research_max_scope_files` | `10` |
| `research_max_source_chars` | `15000` |
| `research_max_per_cycle` | `2` |
| `research_timeout_seconds` | `900` |

### Autosync (implementation)

When `autosync_enabled` is `true`, AIDLC commits (and optionally pushes) on every `autosync_every_implementation_cycles` implementation cycles (default **25**).

| Key | Default |
|---|---|
| `autosync_enabled` | `true` |
| `autosync_every_implementation_cycles` | `25` |
| `autosync_finalize_before_push` | `true` |
| `autosync_push_remote` | `true` |

When `autosync_finalize_before_push` is `true` and `finalize_enabled` is `true`, the same **finalize** passes as end-of-run (`finalize_passes`, or all default passes when `null`) run **before** the autosync commit/push so each pushed checkpoint is cleaned up the same way as the final pipeline. Set to `false` to commit/push without that pass (faster, less polish per interval).

### Finalization

| Key | Default |
|---|---|
| `finalize_enabled` | `true` |
| `finalize_passes` | `null` (all default passes) |
| `finalize_timeout_seconds` | `900` |

## Production Profile Behavior

When `runtime_profile` is `"production"`, these defaults are applied only if the user did not set them explicitly:

- `strict_validation=true`
- `validation_allow_no_tests=false`
- `fail_on_validation_incomplete=true`
- `fail_on_final_test_failure=true`
- `strict_change_detection=true`
- `claude_hard_timeout_seconds=1800`

Additionally, `aidlc run` rejects:

- `--skip-validation`
- `--skip-finalize`

## Notes

- Unknown keys are loaded; they only have effect if runtime code reads them.
- In dry-run mode, planning/implementation cycles are effectively capped when max-cycle settings are left at unlimited.
- `test_profile_mode` values other than `"progressive"` are intentionally unsupported.

## Environment variables

AIDLC does **not** overload the environment for core routing; behavior is driven primarily by `.aidlc/config.json` and CLI flags.

| Variable | Where used |
|----------|------------|
| `EDITOR`, `VISUAL` | `aidlc config edit` (and config subcommand `edit`): opens `config.json` in the chosen editor. |
| `CI` | Full-audit **runtime** subprocesses: set to `1` in the child environment when running build/test/playwright-style checks (`aidlc/audit/runtime_engine.py`). |

Provider authentication uses each vendor’s normal CLI login flow (see provider commands in the CLI help), not a single AIDLC-specific env var.
