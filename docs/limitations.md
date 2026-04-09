# Limitations and Non-Goals

This file documents current intentional constraints.

## Non-Goals

- Backward compatibility shims for removed execution paths.
- Automatic bypass of unmet issue dependencies.
- Automatic breaking of dependency cycles.
- Accepting unstructured implementation output as success.

## Current Operational Limits

- Single-process run lock per target project (`.aidlc/run.lock`).
- Planning and implementation quality depend on model output quality and parseability.
- Test verification is only as strong as the configured or auto-detected test command.
- Audit framework detection is heuristics-based and dependency-file dependent.

## Explicitly Unsupported

- `aidlc run --skip-precheck`
- Automatic success on unstructured implementation output

## Known Tradeoffs

- Planner and implementer fail fast on invalid/contradictory structured actions.
- Full audit uses bounded Claude calls, so very large codebases may be sampled.
