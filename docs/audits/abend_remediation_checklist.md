# Abend Handling Remediation Checklist

Date: 2026-04-15
Scope note: all `project_template` content at repo root and under `aidlc/` was excluded.

## Priority Order

1. Harden verification defaults
- Set `validation_allow_no_tests` to `False` by default.
- Set `strict_change_detection` to `True` by default.
- Consider setting `fail_on_validation_incomplete` to `True` by default for non-dry runs.

2. Remove implicit security downgrade
- Add explicit config gate for plaintext credential fallback.
- Refuse to write plaintext secrets unless user opted in.
- Emit a prominent startup warning when plaintext credential mode is active.

3. Stop masking broken account state
- Backup/quarantine corrupt `accounts.json`.
- Fail routed runs when account metadata cannot be loaded.
- Persist last account validation error instead of just `health_status="unknown"`.

4. Make degraded execution visible
- Add run-state flags for:
  - validation skipped
  - change verification unavailable
  - routing fallback used
  - config refresh failed
  - timeout-stop output accepted
- Surface those flags in checkpoint and final reports.

5. Standardize provider resilience
- Define one retry policy contract for all adapters.
- Either add bounded retries to OpenAI/Copilot adapters or document why they intentionally differ from Claude.

## Quick Wins

- Replace `except Exception: pass` in routing bookkeeping with debug logs.
- Promote account-manager fallback cause to warning logs.
- Include degraded audit counters in executive summaries.
- Mark “accepted after timeout stop request” as degraded success.

## Tests To Add

- Validation fails when no tests are detected under hardened defaults.
- Implementation fails when git change detection is unavailable.
- Corrupt `accounts.json` blocks routed execution.
- Plaintext credential fallback requires explicit opt-in.
- Routing fallback activation is recorded in run state/reporting.

## Documentation To Add

- Production usage guidance: prefer `runtime_profile=production`.
- Security note for credential storage modes.
- Degraded-mode semantics for validation, routing, and finalization metadata.

## Exit Criteria

- No autonomous run can report green while both validation and change verification are absent.
- No secret is written to plaintext storage without explicit opt-in.
- No routed run silently changes auth posture because account state was unreadable.
- All degraded execution paths are visible in reports and state artifacts.
