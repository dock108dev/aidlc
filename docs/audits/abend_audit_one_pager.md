# Abend Handling Audit One-Pager

Date: 2026-04-15
Repo: `aidlc`
Scope note: all `project_template` content at repo root and under `aidlc/` was excluded.

## Executive Summary

Verdict: Prod posture has notable risk areas.

The codebase mostly uses intentional, logged resilience rather than silent suppression. Most catches and fallbacks are operationally reasonable: provider retries are logged, routing fallbacks are surfaced, and audit/detection helpers usually degrade in bounded ways. The notable risks are concentrated in permissive defaults around validation and change detection, plus a security fallback that stores credentials in plaintext when secure storage is unavailable.

Severity counts:
- Critical: 0
- High: 2
- Medium: 4
- Low: 5
- Note: 6

Category counts:
- Catch/log-and-continue: 7
- Silent default/fallback: 5
- Retry/timeout resilience: 4
- Environment/profile strictness: 3
- Security-sensitive suppression: 1
- Observability downgrade/degraded-mode: 3

## Top 5 Issues

1. `VAL-001` Validation can succeed with no tests by default, which allows implementation/finalization to proceed without executable verification. See [aidlc/validator.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/validator.py:60).
2. `IMP-001` Successful implementations can be accepted even when git-based change detection is unavailable or when no file changes are observed, unless strict mode is enabled. See [aidlc/implementer.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/implementer.py:297).
3. `SEC-001` Credential storage falls back to plaintext `~/.aidlc/credentials.json` when `keyring` is unavailable. See [aidlc/accounts/credentials.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/accounts/credentials.py:48).
4. `ACC-001` Corrupt or unreadable `accounts.json` is downgraded to a warning and treated as an empty account set, which can change routing/auth behavior without a hard stop. See [aidlc/accounts/manager.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/accounts/manager.py:54).
5. `RTE-001` Router fallback can synthesize default auth or pick an unavailable adapter after swallowing account-manager failures, preserving liveness at the cost of determinism. See [aidlc/routing/engine.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/routing/engine.py:544) and [aidlc/routing/engine.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/routing/engine.py:628).

## Overall Assessment

What looks good:
- Provider execution failures are usually logged with explicit type/duration/retry context.
- Long-running Claude operations emit periodic warnings and bounded outage retry behavior.
- Production profile defaults tighten validation and change-detection guardrails.
- Audit/config parsing helpers generally fail soft in non-critical paths.

What needs attention:
- Non-production defaults are too permissive for a tool that edits code autonomously.
- Some “continue anyway” paths are appropriate individually, but together they can yield a green-looking run with weak verification.
- Security fallback behavior is documented in code comments but still too permissive for general use.

## Recommended First Actions

- Flip safer defaults for validation/change detection outside `runtime_profile=production`.
- Gate plaintext credential fallback behind an explicit opt-in flag.
- Promote unreadable/corrupt account state from warning to blocking error for routed runs.
- Add telemetry counters for degraded audit parsing and routing/account fallbacks.
- Add regression tests asserting that runs fail closed when verification is unavailable.
