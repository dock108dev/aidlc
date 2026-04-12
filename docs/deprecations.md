# Deprecations

This document tracks behavior intentionally removed from active support.

## Validation Flow

- Non-progressive validation modes are deprecated and removed.
- `test_profile_mode` must be `"progressive"`.
- Runtime now raises an error when legacy mode values are provided.

## Audit-to-Planning Handoff

- Full audit now generates a workload-capped `BRAINDUMP.md`.
- Legacy behavior that implicitly expanded issue/research seeds without workload control is no longer supported.

## Compatibility Policy

- AIDLC prioritizes SSOT behavior over backward compatibility.
- Deprecated branches are removed rather than retained behind legacy toggles.
