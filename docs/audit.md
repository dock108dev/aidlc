# Code Audit Mode

AIDLC supports standalone and pre-run audit modes.

## Commands

- `aidlc audit` - quick audit
- `aidlc audit --full` - quick audit plus Claude-assisted semantic analysis
- `aidlc run --audit` - quick audit before planning
- `aidlc run --audit full` - full audit before planning

## Quick Audit

Quick audit is deterministic and local:

- project type detection
- framework detection from dependency files
- entry point detection
- module listing and role guessing
- source statistics
- tech debt marker scan
- heuristic test coverage assessment

Outputs:

- `STATUS.md` (always generated in target project)
- `ARCHITECTURE.md` (generated only if missing)
- `.aidlc/audit_result.json`
- `.aidlc/CONFLICTS.md` when conflicts are detected

## Full Audit

Full audit includes quick audit plus targeted Claude calls:

- module-level semantic analysis
- feature inventory synthesis

Full audit is constrained by:

- `audit_max_claude_calls`
- `audit_max_source_chars_per_module`

If Claude CLI is unavailable, full mode is rejected for standalone `aidlc audit --full`.

## Conflict Handling

Audit compares generated understanding with user docs and emits conflicts such as:

- project type mismatch in existing `ARCHITECTURE.md`
- references to missing modules
- major modules not mentioned in user docs

Conflicts pause pre-run audit flow in `aidlc run --audit ...` and write `.aidlc/CONFLICTS.md`.
