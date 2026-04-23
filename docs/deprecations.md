# Deprecations

This document tracks behavior intentionally removed from active support.

## Core-focus audit (latest)

The CLI surface and lifecycle were trimmed to the core flow
(BRAINDUMP.md → run → built code). Removed:

- **`aidlc improve`** — duplicated `aidlc run`. To focus a run, write the
  concern into `BRAINDUMP.md`.
- **`aidlc plan`** — interactive multi-doc generator. Replaced by:
  `aidlc init` (scaffolds `BRAINDUMP.md` + `.aidlc/`) plus
  `aidlc init --with-docs` (copies the rest of the doc templates).
- **`aidlc audit`** — folded into `aidlc run --audit`.
- **`aidlc finalize`** — runs as part of `aidlc run`.
- **`aidlc validate`** — runs as part of `aidlc run` (use
  `--skip-validation` to skip).
- **Auditor BRAINDUMP/ARCHITECTURE generation** — auditor is now read-only
  for user-owned docs. Config knobs `audit_braindump_enabled`,
  `audit_braindump_path`, `audit_planning_workload_stop_ratio`,
  `audit_research_estimate_default_hours`, `audit_issue_estimate_defaults`,
  `audit_include_deferred_backlog` are gone. `AuditResult.braindump_summary`
  is gone.
- **`ssot`, `security`, `abend` finalization passes** — vague semantics
  with no clear definition of done. Only `docs` and `cleanup` remain. New
  passes will be reintroduced once their prompts are nailed down.
- **Doc-gap detection on by default** — now opt-in
  (`doc_gap_detection_enabled: false`). It produced spurious planning
  issues on mature repos.
- **`session_dir_max_keep`** — only existed for the retired
  `aidlc plan` wizard.

## Validation Flow

- Non-progressive validation modes are deprecated and removed.
- `test_profile_mode` must be `"progressive"`.
- Runtime now raises an error when legacy mode values are provided.

## Compatibility Policy

- AIDLC prioritizes SSOT behavior over backward compatibility.
- Deprecated branches are removed rather than retained behind legacy toggles.
