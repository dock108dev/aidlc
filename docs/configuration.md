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
| `retry_max_attempts` | `2` |
| `retry_base_delay_seconds` | `30` |
| `retry_max_delay_seconds` | `300` |
| `retry_backoff_factor` | `2.0` |
| `claude_service_outage_max_wait_seconds` | `7200` (2 hours) |

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
| `implementation_complexity_acceptance_criteria_threshold` | `6` |
| `implementation_complexity_dependencies_threshold` | `3` |
| `implementation_complexity_description_chars_threshold` | `2500` |
| `implementation_complexity_labels` | `["architecture", "security", "migration", "refactor-core", "cross-cutting"]` |
| `run_tests_command` | `null` |
| `test_timeout_seconds` | `300` |
| `implementation_allowed_paths` | `null` |
| `strict_change_detection` | `false` |
| `fail_on_final_test_failure` | `false` |

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
