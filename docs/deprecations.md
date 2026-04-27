# Deprecations

This document tracks behavior intentionally removed from active support.

## Core-focus audit

The CLI surface and lifecycle were trimmed to the core flow
(`BRAINDUMP.md` → `aidlc run` → built code).

### Removed CLI subcommands

- **`aidlc improve`** — duplicated `aidlc run`. To focus a run, write the
  concern into `BRAINDUMP.md`.
- **`aidlc plan`** — interactive multi-doc generator. Replaced by
  `aidlc init` (scaffolds `BRAINDUMP.md` + `.aidlc/`); other docs are
  user-authored.
- **`aidlc finalize`** — runs as part of `aidlc run` (use `--skip-finalize`
  to skip).
- **`aidlc validate`** — runs as part of `aidlc run` (use `--skip-validation`
  to skip).
- **`aidlc audit`** — removed without an `aidlc run` integration. The
  underlying engine (`aidlc/auditor.py`, `aidlc/audit/`) is still in the
  codebase as a Python API and is invoked from tests, but no CLI flag
  triggers it. The scanner consumes `.aidlc/audit_result.json` if present
  (e.g. produced externally), so the contract is still useful for
  integrators.

### Auditor BRAINDUMP/ARCHITECTURE generation

The auditor used to overwrite the customer's `BRAINDUMP.md` and
`ARCHITECTURE.md` from heuristics; that inverted the design where
`BRAINDUMP.md` is the customer's voice. The auditor is now read-only for
user-owned docs. Config knobs `audit_braindump_enabled`,
`audit_braindump_path`, `audit_planning_workload_stop_ratio`,
`audit_research_estimate_default_hours`, `audit_issue_estimate_defaults`,
`audit_include_deferred_backlog` are gone, along with
`AuditResult.braindump_summary`.

### Removed finalization passes

`ssot`, `security`, `abend` — vague semantics with no clear definition of
done. Only `docs` and `cleanup` remain. New passes will be reintroduced once
their prompts are nailed down.

### Removed planner action types

`create_doc`, `update_doc`, `research` — discovery and research are now
standalone phases that run before planning, not planning actions. The
canonical action types are defined in
`aidlc/schemas.py:PLANNING_ACTION_TYPES` and consist of `create_issue` and
`update_issue` only.

### Doc-gap detection on by default

Now opt-in (`doc_gap_detection_enabled: false` in DEFAULTS). It produced
spurious planning issues on mature repos.

### Removed config keys

- **`session_dir_max_keep`** — only existed for the retired `aidlc plan`
  wizard.
- **`diminishing_returns_threshold`** — replaced by the SSOT pair
  `planning_diminishing_returns_min_threshold` and `_max_threshold`.
- **`audit_braindump_*`, `audit_planning_workload_*`, `audit_*_estimate_*`**
  — see *Auditor BRAINDUMP/ARCHITECTURE generation* above.
- **`claude_hard_timeout_seconds`** — wall-clock kill on the Claude CLI
  process. Removed because Claude CLI in stream-json mode emits steady
  tool-use events while doing real work — sometimes for an hour or
  more — and the wall-clock kill interrupted productive sessions,
  leaving partial JSON that downstream parsers couldn't handle. Legacy
  config files containing the key are silently ignored. Use
  `claude_stall_kill_seconds` for an activity-based safety valve.
  Non-streaming provider CLIs (Copilot, OpenAI Codex) now use
  `provider_call_timeout_seconds` (default `1800`).

### Removed routing helpers

`is_premium_phase()` / `get_premium_phases()` and the `legacy_premium`
Claude-first routing branches — provider tier preference is now driven
entirely by `providers.<id>.max_capacity` / `max_capacity_weight`.

### Removed `RunPhase.AUDITING`

The deprecated phase value is gone. Old paused state files containing
`phase: "auditing"` will fail to deserialize; use `aidlc reset` and start
fresh, or hand-edit the JSON.

### Removed legacy usage accumulator

`aidlc/cli/usage_cmd.py:_accumulate_legacy_usage` — runs predating
per-provider/account usage telemetry no longer synthesize claude-only
fallback rows. Such runs report "No usage data" instead.

## Validation Flow

- Non-progressive validation modes are deprecated and removed.
- `test_profile_mode` must be `"progressive"`. Other values raise at
  construction time (see `aidlc/validator.py`).

## Compatibility Policy

- AIDLC prioritizes SSOT behavior over backward compatibility.
- Deprecated branches are removed rather than retained behind legacy toggles.
- Where feasible, removals are paired with negative tests asserting the
  symbol stays absent.
