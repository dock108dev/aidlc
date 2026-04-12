# Limitations and Non-Goals

This document captures intentionally unsupported behavior and known operational constraints.

## Intentional Non-Goals

- preserving legacy compatibility branches when SSOT cleanup removes behavior
- automatically bypassing unmet issue dependencies
- automatically resolving/breaking dependency cycles
- treating unstructured implementation output as success
- supporting non-progressive validation modes

## Explicitly Unsupported CLI Behavior

- `aidlc run --skip-precheck`
- in `runtime_profile=production`: `aidlc run --skip-validation`
- in `runtime_profile=production`: `aidlc run --skip-finalize`

## Operational Constraints

- one active run per project (`.aidlc/run.lock`)
- planning/implementation outcomes depend on model output quality and schema conformance
- verification quality depends on configured or detected test commands
- audit and project-type detection are heuristic and file-signature driven
- full audits are bounded by configured Claude-call and source-size caps

## Known Tradeoffs

- planner may fail cycles aggressively on invalid actions or high action-failure ratio
- strict profile settings can pause runs earlier to avoid silent degradation
- scanner and auditor may continue with degraded-read telemetry if some files cannot be parsed/read

## Intentionally Not Automated

- no automatic hidden fallback to previous planning completion criteria
- no forced success when validation remains unstable
- no auto-removal of dependency links to "make progress"
- no compatibility shim for legacy validation flow selection
