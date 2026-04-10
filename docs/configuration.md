# Configuration

Configuration is loaded from `.aidlc/config.json` in the target project (or explicit `--config`).

## Defaults

The canonical defaults are defined in `aidlc/config.py`.

| Key | Default | Used for |
|---|---:|---|
| `runtime_profile` | `"standard"` | runtime strictness preset (`standard` or `production`) |
| `plan_budget_hours` | `4` | planning budget in hours |
| `checkpoint_interval_minutes` | `15` | checkpoint/report cadence |
| `dry_run` | `false` | simulate model calls |
| `claude_cli_command` | `"claude"` | CLI executable |
| `claude_model` | `"opus"` | model label passed through configuration |
| `claude_hard_timeout_seconds` | `0` | hard cap for Claude subprocess (`0` disables cap) |
| `retry_max_attempts` | `2` | model call retry count |
| `retry_base_delay_seconds` | `30` | retry backoff base |
| `retry_max_delay_seconds` | `300` | retry backoff cap |
| `retry_backoff_factor` | `2.0` | retry growth multiplier |
| `max_consecutive_failures` | `3` | planning/implementation failure guard |
| `diminishing_returns_window` | `5` | planning cycle history window |
| `diminishing_returns_threshold` | `3` | no-new-issue threshold to stop planning |
| `finalization_budget_percent` | `10` | planning finalization threshold |
| `max_implementation_attempts` | `3` | per-issue implementation retries |
| `max_planning_cycles` | `0` | planning cap (`0` means unlimited) |
| `max_implementation_cycles` | `0` | implementation cap (`0` means unlimited) |
| `run_tests_command` | `null` | explicit test command override |
| `test_timeout_seconds` | `300` | test command timeout |
| `max_doc_chars` | `10000` | per-doc read cap |
| `max_context_chars` | `80000` | total scanner context cap |
| `max_implementation_context_chars` | `30000` | implementation prompt context cap |
| `doc_scan_patterns` | `["**/*.md","**/*.txt","**/*.rst"]` | scan include patterns |
| `doc_scan_exclude` | built-in excludes | scan exclude patterns |
| `implementation_allowed_paths` | `null` | reserved path-allowlist setting |
| `audit_depth` | `"quick"` | default audit depth |
| `audit_max_claude_calls` | `10` | full-audit model call cap |
| `audit_max_source_chars_per_module` | `15000` | module source cap for full audit |
| `audit_source_extensions` | language set | source extension allowlist |
| `audit_exclude_patterns` | built-in patterns | audit exclusions |
| `strict_validation` | `false` | pause run when validation is incomplete |
| `validation_allow_no_tests` | `true` | allow no-tests-detected to be treated as stable |
| `fail_on_validation_incomplete` | `false` | fail/pause run when validation loop ends unstable |
| `fail_on_final_test_failure` | `false` | fail/pause run when final verification test suite fails |
| `strict_change_detection` | `false` | require verifiable file changes for impl success |
| `planning_action_failure_ratio_threshold` | `0.6` | fail cycle if action failure ratio reaches threshold |

## Notes

- Unknown keys are loaded but only effective if read by runtime modules.
- `max_*_cycles` defaults are unlimited in normal runs and effectively bounded in dry-run paths.
- In `runtime_profile: "production"`, stricter defaults are auto-applied unless explicitly overridden:
  - `strict_validation=true`
  - `validation_allow_no_tests=false`
  - `fail_on_validation_incomplete=true`
  - `fail_on_final_test_failure=true`
  - `strict_change_detection=true`
  - `claude_hard_timeout_seconds=3600`
