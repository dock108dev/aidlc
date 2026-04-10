# AIDLC

AIDLC is a Python CLI for running an AI-assisted development lifecycle inside a target repository.

Core lifecycle in `aidlc run`:

`SCAN -> PLAN -> IMPLEMENT -> VALIDATE -> FINALIZE -> REPORT`

Audit can also run before planning via `--audit`.

## Quick Start

```bash
pip install -e .

# initialize metadata in a target repository
aidlc init --project /path/to/target-repo

# run the lifecycle
aidlc run --project /path/to/target-repo
```

## Requirements

- Python 3.11+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) installed and authenticated (unless using `--dry-run`)

## High-Level Commands

- `aidlc precheck` auto-creates `.aidlc/` if missing and reports documentation readiness tiers
- `aidlc init` initializes `.aidlc/` and optionally copies planning templates with `--with-docs`
- `aidlc audit [--full]` analyzes an existing codebase and writes audit artifacts
- `aidlc run` executes the lifecycle with optional modes (`--resume`, `--plan-only`, `--implement-only`, `--audit`)
- `aidlc finalize` runs finalization passes against the latest run
- `aidlc plan` runs an interactive planning/doc-generation session
- `aidlc improve` runs a targeted improvement cycle from a user concern
- `aidlc status` prints the latest run state

## Runtime Artifacts

```text
.aidlc/
  config.json
  issues/
  runs/<run_id>/
  reports/<run_id>/
```

Additional runtime outputs may include:

- `.aidlc/audit_result.json`
- `.aidlc/CONFLICTS.md`
- `docs/audits/*.md`
- `AIDLC_FUTURES.md`

## Production Profile

Set `runtime_profile` to `"production"` in `.aidlc/config.json` to auto-apply stricter defaults unless explicitly overridden:

- `strict_validation=true`
- `validation_allow_no_tests=false`
- `fail_on_validation_incomplete=true`
- `fail_on_final_test_failure=true`
- `strict_change_detection=true`
- `claude_hard_timeout_seconds=3600`

In production profile, `aidlc run --skip-validation` and `aidlc run --skip-finalize` are rejected.

## Documentation

- `docs/cli-lifecycle.md` - lifecycle phases, stop conditions, and mode behavior
- `docs/configuration.md` - configuration keys and profile behavior
- `docs/audit.md` - audit modes, outputs, and conflict handling
- `docs/local-development.md` - install, test, and packaging workflow for this repo
- `docs/limitations.md` - intentional constraints and non-goals
