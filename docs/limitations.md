# Limitations and Non-Goals

This document captures intentionally unsupported behavior and known
operational constraints.

## Intentional Non-Goals

- preserving legacy compatibility branches when SSOT cleanup removes behavior
- automatically bypassing unmet issue dependencies (the implementer waits
  rather than ignoring a missing dependency)
- treating unstructured implementation output as success
- supporting non-progressive validation modes
- producing the auditor's `STATUS.md` / `.aidlc/audit_result.json` from a
  CLI subcommand (the auditor module remains, but no CLI flag invokes it)

## Explicitly Unsupported CLI Behavior

- `aidlc run --skip-precheck`
- in `runtime_profile=production`: `aidlc run --skip-validation`
- in `runtime_profile=production`: `aidlc run --skip-finalize`

## Automatic Behaviors That Run Without Asking

These run as part of the normal lifecycle and **cannot be disabled**:

- **Dependency-graph normalization** at the end of each planning cycle
  (`aidlc/planner_dependency_graph.py:sanitize_dependencies`):
  drops non-string / empty / self / unknown / duplicate edges, and breaks
  any detected cycle by removing one edge (heuristic: lower-priority /
  heavier-dependency source). Each change emits a `logger.warning`; issue
  markdown is rewritten to reflect the cleaned graph.
- **Cycle-breaking in implementation ordering**
  (`aidlc/implementer_issue_order.py:sort_issues_for_implementation`):
  topologically orders pending issues and removes one edge per detected
  cycle, with a `logger.warning`. Without this, a single bad dependency
  would deadlock the implementer.

If you want strict no-auto-modification of the dependency graph, that mode
does not exist — the lifecycle treats unbreakable cycles as a fatal
condition rather than an acceptable steady state.

## Operational Constraints

- one active run per project (`.aidlc/run.lock`)
- planning/implementation outcomes depend on model output quality and schema
  conformance
- verification quality depends on configured or detected test commands
- project-type detection is heuristic and file-signature driven (see
  `aidlc/scanner.py`)

## Known Tradeoffs

- planner may fail cycles aggressively on invalid actions or high
  action-failure ratio (`planning_action_failure_ratio_threshold`)
- strict profile settings can pause runs earlier to avoid silent degradation
- scanner may continue with degraded-read telemetry if some files cannot be
  parsed/read

## Intentionally Not Automated

- no automatic hidden fallback to previous planning completion criteria
- no forced success when validation remains unstable
- no compatibility shim for legacy validation flow selection
