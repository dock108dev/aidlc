# AIDLC — AI Development Life Cycle

Autonomous planning and implementation framework. Drop it into any repo, point it at your planning docs, and let it plan and build.

## How It Works

```
SCAN → PLAN → IMPLEMENT → DONE
```

1. **SCAN** — Discovers all markdown docs, README, architecture files in your repo. Detects project type (Python, Node, Rust, Go, etc.) and test commands automatically.

2. **PLAN** (time-constrained) — Reads your docs and creates a comprehensive set of issues with full specs, acceptance criteria, priorities, and dependency chains. Runs for a configurable time budget (default: 4 hours).

3. **IMPLEMENT** (completion-constrained) — Picks up every issue created during planning and implements them one by one using Claude. Runs tests after each implementation. Retries failures. **Does not stop until everything is implemented and verified.**

4. **REPORT** — Generates a full run report with stats, issue breakdown, and artifacts.

## Quick Start

```bash
# Install
cd /path/to/aidlc
pip install -e .

# Initialize in your project
cd /path/to/your-project
aidlc init

# Run the full lifecycle (4h planning budget)
aidlc run

# Custom planning budget
aidlc run --plan-budget 2h

# Planning only (review before implementation)
aidlc run --plan-only

# Resume after interruption
aidlc run --resume

# Implement existing issues (skip planning)
aidlc run --implement-only

# Check status
aidlc status
```

## Configuration

After `aidlc init`, edit `.aidlc/config.json`:

```json
{
  "plan_budget_hours": 4,
  "checkpoint_interval_minutes": 15,
  "claude_model": "opus",
  "max_implementation_attempts": 3,
  "run_tests_command": "npm test"
}
```

Key options:

| Option | Default | Description |
|--------|---------|-------------|
| `plan_budget_hours` | 4 | Planning phase time budget |
| `claude_model` | opus | Claude model to use |
| `max_implementation_attempts` | 3 | Retries per issue |
| `run_tests_command` | auto-detect | Test command (e.g., `npm test`, `pytest`) |
| `checkpoint_interval_minutes` | 15 | State checkpoint frequency |
| `finalization_budget_percent` | 10 | % of plan budget reserved for finalization |
| `dry_run` | false | Simulate without Claude calls |
| `doc_scan_patterns` | `["**/*.md"]` | Glob patterns for doc discovery |
| `doc_scan_exclude` | `[node_modules, ...]` | Directories to skip |

## Project Structure

```
.aidlc/                    # Created in your project
  config.json              # Your configuration
  issues/                  # Generated issue specs (markdown)
  runs/<run_id>/           # Per-run state, logs, checkpoints
  reports/<run_id>/        # Per-run reports
```

## How Planning Works

The planner scans your repo's documentation and iteratively builds a set of issues:

- Reads README, architecture docs, design docs, roadmaps
- Creates issues with titles, descriptions, acceptance criteria, priorities
- Defines dependency chains between issues
- Creates supplementary design docs when needed
- Enters finalization mode in the last 10% of budget to refine and close gaps

## How Implementation Works

The implementer picks up issues in dependency/priority order and:

1. Builds a context-rich prompt with the issue spec + project context
2. Calls Claude with file edit permissions to implement
3. Runs the test suite to verify
4. If tests fail, gives Claude a chance to fix
5. Retries failed issues (up to `max_implementation_attempts`)
6. Continues until every issue is implemented or exhausted
7. Runs a final verification pass

## Supported Project Types

Auto-detects test commands for:
- **Python**: `pytest`
- **Node.js**: `npm test`
- **Rust**: `cargo test`
- **Go**: `go test ./...`
- **Ruby**: `bundle exec rspec`
- **Make**: `make test`

Set `run_tests_command` in config to override.

## Resume & Rerun

- **Ctrl+C** during a run saves state. `aidlc run --resume` picks up where you left off.
- **Rerun**: Delete `.aidlc/runs/` and run again, or just start a new run (previous runs are preserved).
- **Plan then review**: Use `--plan-only`, review issues in `.aidlc/issues/`, then `--implement-only`.

## Requirements

- Python 3.11+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) installed and authenticated
