# Configuration

Configuration is loaded from `.aidlc/config.json` in the target repository, unless `--config` is provided.

Canonical defaults live in `aidlc/config.py` (the `DEFAULTS` dict).

## Resolution Rules

1. start with built-in defaults
2. merge user config from `.aidlc/config.json` (or explicit config file)
3. set internal runtime paths (`_project_root`, `_aidlc_dir`, `_runs_dir`, `_reports_dir`, `_issues_dir`)
4. if `runtime_profile == "production"`, apply strict profile defaults for keys not explicitly set by the user

The `providers` sub-dict deep-merges: per-provider entries you don't set keep their defaults (`cli_command`, `default_model`, `phase_models`, `model_fallback_chain`). Within `providers.<id>`, the `phase_models` sub-dict also deep-merges, so you can override a single phase without resetting the others.

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
| `routing_impl_budget_explore_probability` | `0.05` |
| `routing_rate_limit_cooldown_seconds` | `300` |
| `routing_rate_limit_buffer_base_seconds` | `3600` (1h) |

On HTTP 429 / rate-limit responses, the router waits until the provider-reported reset time **plus** a buffer: `base × 1`, `base × 2`, `base × 4` … capped at **`base × 8`** hours per consecutive rate limit on the same provider/model (resets after a successful call). Set `routing_rate_limit_buffer_base_seconds` to `0` to disable the extra wait when no time is reported (tests / debugging).

**Per-provider routing** lives under `providers.<id>` in the same JSON. The default block looks like:

```json
{
  "providers": {
    "claude": {
      "enabled": true,
      "cli_command": "claude",
      "max_capacity": true,
      "max_capacity_weight": 20,
      "default_model": "sonnet",
      "phase_models": {
        "planning": "sonnet",
        "research": "sonnet",
        "implementation": "sonnet",
        "implementation_complex": "opus",
        "finalization": "sonnet",
        "audit": "sonnet"
      },
      "model_fallback_chain": ["sonnet", "opus", "haiku"]
    }
  }
}
```

| Key | Meaning |
|---|---|
| `enabled` | Master switch for the provider. Disabled providers are skipped entirely. |
| `cli_command` | Path/name of the CLI binary. |
| `max_capacity` | Mark a backend as **high token capacity** (vs. Copilot/OpenAI-style). Default `true` for `claude`, `false` otherwise. |
| `max_capacity_weight` | On planning/research/audit, balanced mode rotates by weighted fairness: lower `calls ÷ weight` is preferred, so a weight-20 provider gets ~20× the first-choice share over time. Default `20` when `max_capacity` is true. |
| `default_model` | Fallback model when no `phase_models[phase]` entry resolves. **A user-set value overrides DEFAULT `phase_models` entries** — see precedence below. |
| `phase_models` | Per-phase model selection. Keys: `planning`, `research`, `implementation`, `implementation_complex`, `finalization`, `audit`. |
| `model_fallback_chain` | Ordered list of models to try on the same provider when one returns "out of tokens". Default for Claude: `["sonnet", "opus", "haiku"]`. Empty/missing chain disables intra-provider fallback (router excludes the provider on first exhaustion). |

For **`implementation`** and **`implementation_complex`**, every provider with `max_capacity: true` is ordered **before** other providers (stable order: claude → copilot → openai among those enabled). Model IDs per phase are still driven by `phase_models` — this only chooses **which CLI** runs first.

### Model selection precedence

When the router needs a model for a given phase, it walks this order and uses the first non-empty value:

1. `providers.<id>.phase_models[phase]` — *user-set* (i.e., present in your `.aidlc/config.json`)
2. `providers.<id>.default_model` — *user-set*
3. `providers.<id>.phase_models[phase]` — DEFAULT (from `aidlc/config.py`)
4. `providers.<id>.default_model` — DEFAULT
5. Adapter fallback (whatever the provider's CLI ships with)

The key rule: **a user-set `default_model` beats DEFAULT `phase_models`**. So setting `{"providers": {"claude": {"default_model": "opus"}}}` is enough to force opus across all phases — you do not need to override every `phase_models` entry separately. The router debug log shows the chosen model and its source on each call.

### Token-exhaustion fallback chain

When a model returns `out of tokens` / quota exceeded:

1. The router consults `providers.<id>.model_fallback_chain`.
2. The next entry in the chain (not already excluded for this provider in this run) is tried with the same provider.
3. If the chain is exhausted, the entire provider is excluded and the router moves to the next enabled provider per the routing strategy.
4. If all providers are exhausted, the run stops with `stop_reason` set; with the default `stop_on_all_models_token_exhausted: true`, the runner exits cleanly so you can resume after billing/quota recovers.

The "Stopping run" log line includes the chain attempted, e.g.:
`Stopping: claude exhausted [sonnet, opus, haiku]; no remaining providers.`

### Claude Execution

| Key | Default |
|---|---|
| `claude_long_run_warn_seconds` | `300` (warn every N seconds if Claude is still running) |
| `claude_hard_timeout_seconds` | `1800` (30 minutes) |
| `claude_timeout_grace_seconds` | `30` (graceful shutdown window before SIGKILL) |
| `telemetry_cost_mode` | `"auto"` |
| `telemetry_estimate_usd` | `false` |
| `telemetry_model_pricing_usd_per_million_tokens` | See `aidlc/config.py` for the full table (sonnet/opus/haiku/gpt-5.4 family). |
| `retry_max_attempts` | `2` |
| `retry_base_delay_seconds` | `30` |
| `retry_max_delay_seconds` | `300` |
| `retry_backoff_factor` | `2.0` |
| `claude_service_outage_max_wait_seconds` | `7200` (keep retrying on 5xx for up to 2h) |

`telemetry_cost_mode` values:

- `auto`: use exact CLI cost metadata when available, estimate otherwise (only if `telemetry_estimate_usd` is true)
- `exact_only`: track only exact cost metadata (no fallback estimates)
- `estimate_only`: always estimate using pricing table and token counts (ignores `telemetry_estimate_usd`)

`telemetry_estimate_usd`: when `false` (default), token counts are still recorded but **no** USD is computed from `telemetry_model_pricing_*` in `auto` mode (API list $/M is not a subscription bill). Set `true` if you want rough API-reference dollar estimates.

> **Note on legacy flat keys.** Some prior versions of this doc listed `claude_model`, `claude_model_planning`, `claude_model_implementation`, etc. at the top level of the config. Those keys do **not** exist in DEFAULTS and have no effect. Use `providers.claude.default_model` and `providers.claude.phase_models.<phase>` instead.

### Planning and Context

| Key | Default |
|---|---|
| `diminishing_returns_window` | `5` |
| `diminishing_returns_threshold` | `2` (deprecated — use the new min/max keys below) |
| `planning_diminishing_returns_min_threshold` | `3` |
| `planning_diminishing_returns_max_threshold` | `6` |
| `finalization_budget_percent` | `10` |
| `planning_finalization_grace_cycles` | `1` |
| `planning_doc_min_chars` | `800` |
| `planning_action_failure_ratio_threshold` | `0.6` |
| `max_doc_chars` | `10000` |
| `max_context_chars` | `40000` |
| `max_implementation_context_chars` | `12000` |
| `project_brief_max_chars` | `20000` |
| `phase_context_max_chars` | `20000` |
| `max_planning_prompt_chars` | `60000` |
| `planning_issue_index_max_items` | `15` |
| `planning_issue_index_include_all_until` | `12` |
| `planning_last_cycle_notes_max_chars` | `300` |
| `doc_scan_patterns` | `["**/*.md", "**/*.txt", "**/*.rst"]` |
| `doc_scan_exclude` | `["node_modules/**", ".git/**", "venv/**", ".venv/**", "__pycache__/**", ".aidlc/**", "dist/**", "build/**"]` |
| `doc_gap_detection_enabled` | `false` |
| `doc_gap_max_items` | `50` |

**Adaptive diminishing-returns threshold.** The planner exits when it sees N consecutive cycles with zero new issues. N is now adaptive to issue count: `N = clamp(min, ceil(num_issues_so_far / 10), max)`. So a small project (≤30 issues) uses 3, a large project (≥60 issues) uses 6. The legacy `diminishing_returns_threshold` is still read with a deprecation log; remove it from your config when you're ready.

**Doc-gap detection (opt-in).** Off by default — on mature repos, scanning every doc for TBD/placeholder markers and turning them into spurious planning issues created noise. Set `doc_gap_detection_enabled: true` on greenfield projects where doc gaps are real planning input.

**Session pruning** has been removed. The `session_dir_max_keep` knob existed only for the retired `aidlc plan` wizard.

### Implementation and Testing

| Key | Default |
|---|---|
| `max_implementation_attempts` | `3` |
| `implementation_escalate_on_retry` | `true` |
| `implementation_reopen_verified_without_result` | `true` |
| `implementation_accept_pre_existing_suite_failures` | `true` |
| `implementation_pre_existing_debt_min_chars` | `40` |
| `implementation_pre_existing_prose_heuristic` | `true` |
| `implementation_use_targeted_tests_when_suite_unstable` | `true` |
| `implementation_targeted_test_command` | `null` |
| `implementation_complexity_acceptance_criteria_threshold` | `12` |
| `implementation_complexity_dependencies_threshold` | `5` |
| `implementation_complexity_description_chars_threshold` | `5000` |
| `implementation_complexity_labels` | `["architecture", "security", "migration", "refactor-core", "cross-cutting"]` |
| `implementation_completed_issues_max` | `12` |
| `implementation_finalize_on_early_stop` | `false` |
| `run_tests_command` | `null` |
| `test_timeout_seconds` | `300` |
| `implementation_allowed_paths` | `null` |
| `strict_change_detection` | `false` |
| `fail_on_final_test_failure` | `false` |
| `stop_on_all_models_token_exhausted` | `true` |

**Early-stop finalization (default off).** When implementation stops with work remaining (token exhaustion, dependency cycle, consecutive failures), the implementer **does not** auto-run finalization passes by default. Set `implementation_finalize_on_early_stop: true` to opt in; that runs the `cleanup` pass only (the legacy `ssot`/`abend` passes were removed in the core-focus audit). The default exits cleanly with a single-line `STOP REASON: ...` and `RESUME WITH: aidlc run --resume` so you can pick up after the underlying issue (e.g., billing) is resolved.

After implementation, if `run_tests_command` fails, AIDLC runs a **fix-tests** prompt. If tests still fail, but the model documents **pre-existing / unrelated** suite failures — ideally via structured JSON (`failures_are_pre_existing_unrelated` + `follow_up_documentation`) — the issue can still be marked **implemented** when `implementation_accept_pre_existing_suite_failures` is `true` and the documentation is at least `implementation_pre_existing_debt_min_chars` long — notes are appended for follow-up issues. If the model omits JSON, `implementation_pre_existing_prose_heuristic` (default `true`) treats clear prose (e.g. "pre-existing unrelated suite", "gate is blocked") as documentation. Set `implementation_accept_pre_existing_suite_failures` to `false` to require a green test command for every issue.

When that happens, the run records that the **project-wide test gate is unstable**. On later implementation cycles (post-implementation tests and fix-tests re-runs), if `implementation_use_targeted_tests_when_suite_unstable` is `true`, AIDLC may replace the configured command with a **narrower** one: for Godot/GUT-style commands it appends `-gtest=` with paths derived from files changed in that issue (plus sibling `test_*.gd` in the same directory). Set `implementation_targeted_test_command` to a shell string template (optional `{gtest_paths}` / `{paths}` placeholders) to override that behavior. **Final verification** (`_verification_pass`) still runs the full `run_tests_command` unchanged so you do not silently lose a full-suite signal at the end of a session.

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

The auditor used to write `BRAINDUMP.md` and a workload-capped seed plan based on `audit_braindump_*` / `audit_planning_workload_*` / `audit_*_estimate_*` knobs. Those knobs were removed in the core-focus audit because the auditor was overwriting the customer's `BRAINDUMP.md`. The auditor is now read-only for user-owned docs; it writes only `STATUS.md` and `.aidlc/audit_result.json`.

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
| `autosync_commit_message_template` | `"aidlc: autosync after implementation cycle {cycle}"` |
| `autosync_issue_status_sync` | `true` |
| `autosync_prune_enabled` | `true` |
| `autosync_runs_to_keep` | `5` |
| `autosync_keep_claude_outputs` | `200` |

When `autosync_finalize_before_push` is `true` and `finalize_enabled` is `true`, the same **finalize** passes as end-of-run (`finalize_passes`, or all default passes when `null`) run **before** the autosync commit/push so each pushed checkpoint is cleaned up the same way as the final pipeline. Set to `false` to commit/push without that pass (faster, less polish per interval).

### Finalization

| Key | Default |
|---|---|
| `finalize_enabled` | `true` |
| `finalize_passes` | `null` (all default passes) |
| `finalize_timeout_seconds` | `900` |
| `finalize_project_context_max_chars` | `22000` |

### Resume / reconcile

| Key | Default |
|---|---|
| `resume_reconcile_enabled` | `true` |

When you resume a run that's past planning, AIDLC will best-effort mark issues as implemented when their ID appears in the git tree outside `.aidlc/`. Disable with `resume_reconcile_enabled: false`.

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

Provider authentication uses each vendor's normal CLI login flow (see provider commands in the CLI help), not a single AIDLC-specific env var.
