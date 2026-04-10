# AIDLC — AI Development Life Cycle

AIDLC is a CLI that runs a four-phase workflow against a target repository:

`SCAN -> PLAN -> IMPLEMENT -> REPORT`

## Quick Start

```bash
pip install -e .

# initialize AIDLC metadata in the target repository
aidlc init --project /path/to/target-repo

# run full lifecycle
aidlc run --project /path/to/target-repo
```

## Local Development

```bash
# run tests
python -m pytest
```

## Command Summary

- `aidlc precheck` checks required planning docs and auto-initializes `.aidlc/` when missing.
- `aidlc init` creates `.aidlc/` and optional planning templates.
- `aidlc audit [--full]` generates `STATUS.md` and optionally `ARCHITECTURE.md` in the target repo.
- `aidlc run` executes scan, planning, implementation, and reporting.
- `aidlc status` prints state of the latest run.

## Runtime Behavior Highlights

- `aidlc run` enforces precheck unless running in `--resume` or `--implement-only` mode.
- Planning fails a cycle on schema validation errors or action application errors.
- Implementation requires structured JSON results from the model; unstructured success fallback is removed.
- Dependency cycles and unmet dependencies are treated as stop conditions (not bypassed).
- Claude calls use retry + backoff for transient failures and can enforce a hard timeout via config.
- Validation/finalization strictness is profile-driven (`runtime_profile: "standard" | "production"`).

## Production Safety Profile

Use `runtime_profile: "production"` in `.aidlc/config.json` to enable stricter defaults:

- `strict_validation: true`
- `validation_allow_no_tests: false`
- `fail_on_validation_incomplete: true`
- `fail_on_final_test_failure: true`
- `strict_change_detection: true`
- `claude_hard_timeout_seconds: 3600`

When running in production profile:

- `aidlc run --skip-validation` is rejected.
- `aidlc run --skip-finalize` is rejected.
- final verification/test failures pause the run instead of completing successfully.

## Run Artifacts

```text
.aidlc/
  config.json
  issues/
  runs/<run_id>/
  reports/<run_id>/
```

## Deployment Basics

- This repository ships a Python CLI package (`aidlc`) with entrypoint `aidlc`.
- Deployment is standard Python packaging/publishing workflow (build and publish package, then install in target environments).
- Runtime state is stored in each target project's `.aidlc/` directory, not in a central service.

## Documentation Map

- `docs/cli-lifecycle.md` - lifecycle semantics, stop conditions, and phase rules
- `docs/configuration.md` - config keys and defaults from `aidlc/config.py`
- `docs/audit.md` - quick/full audit behavior and generated artifacts
- `docs/limitations.md` - explicit non-goals and intentionally unsupported paths

Template markdown under `project_template/` and `aidlc/project_template/` is scaffolding content for generated project docs, not canonical repository operation docs.

## Requirements

- Python 3.11+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) installed and authenticated
