# Abend Handling Audit Report

Date: 2026-04-15
Repo: `aidlc`
Scope note: all `project_template` content at repo root and under `aidlc/` was excluded.

## Section 1: Executive Summary

Overall assessment:

The production-path suppression posture is mixed but understandable. Most handling is intentional resilience rather than accidental swallowing: provider CLIs retry outages, routing warns when quality degrades, and audit/config helpers degrade in bounded ways. The main concern is not hidden exceptions in the hot path so much as permissive defaults that allow the system to keep moving when validation or state integrity is weak.

Severity counts:

| Severity | Count |
|---|---:|
| Critical | 0 |
| High | 2 |
| Medium | 4 |
| Low | 5 |
| Note | 6 |

Category counts:

| Category | Count |
|---|---:|
| Exception catch / log-and-continue | 7 |
| Silent default / fallback | 5 |
| Retry / timeout / best-effort | 4 |
| Strictness changed by config/profile | 3 |
| Security-sensitive suppression | 1 |
| Observability blind spot / degraded telemetry | 3 |

Current production suppressions appear mostly acceptable or whether there are notable risks:

There are notable risks. The code is not broadly unsafe, but it does have a few fail-open behaviors that can make an autonomous run look healthier than it is:
- validation can be skipped entirely by default,
- successful implementation can survive missing change verification,
- account/credential fallbacks can silently alter auth posture,
- degraded audit parsing is often tracked only weakly.

Top 5 issues to address first:

1. `VAL-001` No-tests validation skip by default in [aidlc/validator.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/validator.py:60)
2. `IMP-001` Soft change-detection failure in [aidlc/implementer.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/implementer.py:297)
3. `SEC-001` Plaintext credential fallback in [aidlc/accounts/credentials.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/accounts/credentials.py:48)
4. `ACC-001` Empty-account fallback on corrupt state in [aidlc/accounts/manager.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/accounts/manager.py:54)
5. `RTE-001` Routing/account fallback after swallowed state failures in [aidlc/routing/engine.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/routing/engine.py:544)

## Section 2: Detailed Findings Table

| ID | File Path | Function / Area | Category | Exact Behavior | Trigger / Failure Mode | Current Handling | Prod Impact | Observability Impact | Data Integrity Risk | Security Risk | Reliability Risk | Recommended Disposition | Severity | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `VAL-001` | `aidlc/validator.py` | `Validator.run` | silent default / strictness | Skips validation and returns success when no test commands are detected by default | repo has no detected tests or detection misses them | info log then `True` unless strict flags are enabled | can mark repo stable without tests | medium | medium | low | high | fail closed by default or require explicit opt-in | High | High |
| `IMP-001` | `aidlc/implementer.py` | `_implement_issue` | log-and-continue / strictness | Accepts implementation success even when git verification is unavailable or no file changes are observed unless strict mode is enabled | git unavailable, git timeout, or model reports success without edits | warning only in non-strict mode | can mark issue implemented with weak evidence | high | high | low | high | make strict change detection the default | High | High |
| `SEC-001` | `aidlc/accounts/credentials.py` | `CredentialStore.store/get` | security-sensitive fallback | Falls back from keyring to plaintext credential file | keyring missing or keyring call fails | one warning, then plaintext `credentials.json` with chmod 600 | secrets can persist in weaker store | medium | low | High | medium | require explicit config flag for plaintext fallback | High | High |
| `ACC-001` | `aidlc/accounts/manager.py` | `_load_all` | catch/log-and-continue | Corrupt/unreadable account state becomes empty account map | `accounts.json` unreadable or invalid | warning and `{}` | routing/auth may change silently | high | low | low | medium | treat as blocking for routed runs or quarantine the file | Medium | High |
| `RTE-001` | `aidlc/routing/engine.py` | `_get_accounts_for_provider`, `_fallback_decision` | fallback / swallowed error | Swallows account-manager failures and synthesizes default account or unavailable adapter fallback | account manager exception or all providers unavailable | silent/sparse logging, continue with fallback | model/provider choice may diverge from configured accounts | medium | medium | low | medium | log fallback cause explicitly and expose degraded routing state | Medium | High |
| `FIN-001` | `aidlc/finalizer.py` | `_refresh_config` | broad except / log-and-continue | Any exception during post-finalization config refresh is downgraded to warning | detect/merge/update failure | warning only | config may drift after finalization | medium | medium | low | low | narrow exception types and persist refresh failure in run state | Medium | Medium |
| `FIN-002` | `aidlc/finalizer.py` | `_get_diff_summary`, `_write_futures_note` | best effort | Git failures are swallowed and diff/branch metadata is omitted | git missing, timeout | no diff summary / branch becomes `unknown` | no direct runtime risk | medium | low | low | low | acceptable; add debug context | Low | High |
| `CLA-001` | `aidlc/claude_cli.py` | `execute_prompt` | retry / timeout resilience | Retries transient failures and service outages with backoff for up to 2h | 5xx/network/transient CLI failures | warning/info logs, eventual failure result | good liveness, but long delays possible | low | low | low | low | acceptable; surface retry budget exhaustion clearly in reports | Note | High |
| `CLA-002` | `aidlc/claude_cli.py` | `execute_prompt` | downgraded timeout | Accepts output if CLI exits cleanly after timeout stop request | hard timeout reached but process exits 0 during grace period | info log and success path | partial output could be accepted | medium | medium | low | medium | attach explicit `timed_out_but_accepted` marker to result | Medium | Medium |
| `COP-001` | `aidlc/providers/copilot_adapter.py` | `validate_health` | swallowed non-critical probe | Help probe exceptions are silently ignored after version passes | `copilot --help` fails/hangs unexpectedly | bare `except Exception: pass` | little direct impact | low | low | low | low | acceptable note; drop dead probe or debug-log failures | Note | High |
| `OAI-001` | `aidlc/providers/openai_adapter.py` | `execute_prompt` | no-retry fallback | Transient OpenAI CLI failures are classified but not retried locally | 429/503/timeout text in stderr | failure result returned immediately | more brittle than Claude path | medium | medium | low | medium | add bounded retries or document router-level expectation | Low | Medium |
| `CFG-001` | `aidlc/config_detect.py` | `_detect_lint_command` | warning-and-continue | Package/pyproject parse failures suppress lint auto-detection | unreadable/invalid manifest | warning and continue | lint command may remain unset | low | low | low | low | acceptable | Note | High |
| `CFG-002` | `aidlc/config_detect.py` | `update_config_file` | fail closed | Corrupt config refuses auto-merge and writes backup | invalid JSON in `.aidlc/config.json` | logs error and raises | protects config integrity | good | none | none | none | keep as-is | Note | High |
| `AUD-001` | `aidlc/audit/quick_engine.py` | dependency/entrypoint parsing helpers | degraded-mode tracking | Read/parse failures are converted into degraded counters without direct logs | OSError / JSON parse failures while scanning | `_mark_degraded(...)` and continue | audit result may be incomplete | medium | medium | low | low | surface degraded counters in top-level audit summary | Low | High |
| `AUD-002` | `aidlc/audit/runtime_engine.py` | `_run_command` | log-and-continue | Runtime audit command timeout/not-found becomes failed tier with excerpt | missing tool or long-running test | warning and failed tier result | acceptable for audit mode | low | low | low | low | keep as-is | Note | High |
| `ACC-002` | `aidlc/accounts/manager.py` | `validate` | warning-and-continue | Adapter health-check exceptions set account health to `unknown` instead of failing | provider health probe throws | warning then update account | can hide exact auth error cause | medium | medium | low | medium | persist failure detail alongside `unknown` | Low | Medium |
| `RTE-002` | `aidlc/routing/engine.py` | `execute_prompt` | swallowed bookkeeping error | `mark_used` failures are suppressed silently | account state write/update failure | bare except pass | no direct execution impact | low | medium | low | low | debug-log at minimum | Low | High |

## Section 3: Finding Details

### `VAL-001` Validation can be skipped entirely

Code locations:
- [aidlc/validator.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/validator.py:60)
- [aidlc/config.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/config.py:221)

Paraphrase:
- If no test commands are detected, validation logs an info message and returns success unless `strict_validation=True` and `validation_allow_no_tests=False`.

Why this exists:
- The tool is designed to work across repos with uneven test maturity.

Whether it appears intentional:
- Yes. The config keys and production profile override make this explicit.

Why it may be safe:
- For bootstrap or exploratory runs, forcing validation may be counterproductive.

Why it may be risky:
- In a code-editing agent, “no tests detected” is not strong evidence that no tests exist. Mis-detection and absent configuration can turn into false green runs.

Whether prod behavior is appropriate:
- Only when `runtime_profile=production` is actually set. The default posture is too permissive.

Recommendation:
- Make this fail closed by default, or at minimum emit a high-visibility warning and mark run outcome as degraded rather than successful.

### `IMP-001` Implementation success can survive weak change verification

Code locations:
- [aidlc/implementer.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/implementer.py:297)
- [aidlc/implementer.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/implementer.py:538)

Paraphrase:
- After a model reports success, git-based change detection is attempted.
- If git is unavailable or no files changed, the code only warns unless `strict_change_detection=True`.

Why this exists:
- To keep implementation moving in non-git or degraded environments.

Whether it appears intentional:
- Yes.

Why it may be safe:
- Some issue work may legitimately be docless/no-op in dry-run scenarios.

Why it may be risky:
- In real runs this is a hidden-integrity risk: the system can claim an issue was implemented without hard evidence of durable edits.

Whether prod behavior is appropriate:
- Appropriate only under strict mode. Non-strict default is too soft for autonomous edits.

Recommendation:
- Turn on strict change detection by default and record a degraded run state when git verification is impossible.

### `SEC-001` Credentials fall back to plaintext storage

Code locations:
- [aidlc/accounts/credentials.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/accounts/credentials.py:48)
- [aidlc/accounts/credentials.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/accounts/credentials.py:107)

Paraphrase:
- If `keyring` is missing or keyring calls fail, credentials are stored in `~/.aidlc/credentials.json`, with one warning and chmod `0600`.

Why this exists:
- To support CI/non-GUI environments and keep account features usable.

Whether it appears intentional:
- Yes, explicitly documented.

Why it may be safe:
- File permissions are restricted and the behavior is warned.

Why it may be risky:
- Plaintext at rest is still plaintext. Keyring outages or missing dependencies silently reduce the storage security model.

Whether prod behavior is appropriate:
- Not as an implicit default.

Recommendation:
- Require explicit `allow_plaintext_credentials=true` or equivalent before using the file fallback.

### `ACC-001` Corrupt account state becomes empty state

Code locations:
- [aidlc/accounts/manager.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/accounts/manager.py:54)

Paraphrase:
- Unreadable or invalid `accounts.json` logs a warning and returns `{}`.

Why this exists:
- To avoid crashing CLI operations on state-file corruption.

Whether it appears intentional:
- Yes.

Why it may be safe:
- For read-only status commands, empty state is survivable.

Why it may be risky:
- For routing, empty accounts means different auth selection and potentially different provider behavior, without a hard stop.

Whether prod behavior is appropriate:
- Not for routed execution.

Recommendation:
- Quarantine corrupt account state, back it up, and fail routed operations until repaired.

### `RTE-001` Router continues after account-state failure

Code locations:
- [aidlc/routing/engine.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/routing/engine.py:544)
- [aidlc/routing/engine.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/routing/engine.py:628)

Paraphrase:
- If account lookup fails, routing synthesizes a default account.
- If all providers are unavailable, it still manufactures a fallback route, sometimes to an unavailable adapter.

Why this exists:
- Preserve liveness and keep `execute_prompt()` behavior uniform.

Whether it appears intentional:
- Yes.

Why it may be safe:
- The execution result still carries provider/fallback metadata and eventual provider errors should surface.

Why it may be risky:
- It weakens determinism. Silent fallback from configured account routing to default auth can hide auth misconfiguration or account corruption.

Whether prod behavior is appropriate:
- Acceptable only if the degraded routing state is very visible.

Recommendation:
- Promote account-manager exceptions to explicit warning logs and mark run state as degraded.

### `FIN-001` Config refresh failure is warning-only

Code locations:
- [aidlc/finalizer.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/finalizer.py:153)

Paraphrase:
- Any exception during post-finalization config detection/merge is caught and logged as a warning.

Why this exists:
- Finalization should not fail solely because config auto-detection failed.

Whether it appears intentional:
- Yes.

Why it may be safe:
- It is post-processing, not the main execution path.

Why it may be risky:
- The repo may have changed materially, but the stored config remains stale with little follow-up visibility.

Whether prod behavior is appropriate:
- Mostly yes, but it should leave stronger state/report evidence.

Recommendation:
- Narrow exception types and write refresh-failure details into the report/run state.

### `FIN-002` Git metadata during finalization is best-effort

Code locations:
- [aidlc/finalizer.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/finalizer.py:164)
- [aidlc/finalizer.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/finalizer.py:209)

Paraphrase:
- Git timeout or missing binary yields empty diff summary or `unknown` branch with no escalation.

Why this exists:
- Finalization report generation should not fail on git metadata alone.

Whether it appears intentional:
- Yes.

Why it may be safe:
- This affects audit richness, not correctness of core implementation.

Why it may be risky:
- Audit reports lose context.

Whether prod behavior is appropriate:
- Yes.

Recommendation:
- Keep, but add debug breadcrumbs in report metadata.

### `CLA-001` Claude retries service outages and transient failures

Code locations:
- [aidlc/claude_cli.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/claude_cli.py:120)

Paraphrase:
- Transient/provider-outage failures are classified, logged, and retried with exponential backoff. Service-down retries can continue for up to 2 hours.

Why this exists:
- External CLI/provider instability is common and often transient.

Whether it appears intentional:
- Yes.

Why it may be safe:
- Failures are not silent; retry timing and exhaustion are logged.

Why it may be risky:
- Long waiting windows may delay pipeline completion.

Whether prod behavior is appropriate:
- Yes, assuming operators expect long-lived runs.

Recommendation:
- Keep. Add summary telemetry for retry budgets consumed.

### `CLA-002` Timeout-stop success is accepted

Code locations:
- [aidlc/claude_cli.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/claude_cli.py:171)
- [aidlc/claude_cli.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/claude_cli.py:200)

Paraphrase:
- If the process exits `0` after a timeout-triggered graceful stop request, the output is accepted as successful.

Why this exists:
- Salvage usable output after the tool cooperatively stops.

Whether it appears intentional:
- Yes.

Why it may be safe:
- The model may have completed enough work before interruption.

Why it may be risky:
- Accepted output may be partial while appearing normal.

Whether prod behavior is appropriate:
- Borderline.

Recommendation:
- Keep the salvage behavior but annotate the result as partial/degraded.

### `COP-001` Copilot health probe silently ignores secondary probe failure

Code locations:
- [aidlc/providers/copilot_adapter.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/providers/copilot_adapter.py:165)

Paraphrase:
- After a successful `copilot version` check, exceptions from a `copilot --help` probe are ignored.

Why this exists:
- The help probe is non-critical.

Whether it appears intentional:
- Yes.

Why it may be safe:
- Version is the real availability signal here.

Why it may be risky:
- Tiny observability gap only.

Whether prod behavior is appropriate:
- Yes.

Recommendation:
- Either remove the help probe or debug-log failures.

### `OAI-001` OpenAI adapter classifies transient failures but does not retry

Code locations:
- [aidlc/providers/openai_adapter.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/providers/openai_adapter.py:88)

Paraphrase:
- 429/503/timeout-like stderr gets classified as `transient`, but no local retry loop exists.

Why this exists:
- Simpler adapter path than Claude.

Whether it appears intentional:
- Appears intentional but undocumented.

Why it may be safe:
- The caller can react to failure type.

Why it may be risky:
- Inconsistent resilience across providers can create noisy or brittle runs.

Whether prod behavior is appropriate:
- Acceptable, but weaker than the Claude path.

Recommendation:
- Standardize retry policy across adapters or clearly document the asymmetry.

### `CFG-001` Lint auto-detection failures are warning-only

Code locations:
- [aidlc/config_detect.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/config_detect.py:150)

Paraphrase:
- Manifest parse/read failures only suppress lint detection.

Why this exists:
- Auto-detect is advisory.

Whether it appears intentional:
- Yes.

Why it may be safe:
- No core runtime behavior depends on this.

Why it may be risky:
- Minimal.

Whether prod behavior is appropriate:
- Yes.

Recommendation:
- Keep as-is.

### `CFG-002` Corrupt config fails closed during auto-merge

Code locations:
- [aidlc/config_detect.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/config_detect.py:78)

Paraphrase:
- Invalid JSON in `.aidlc/config.json` is backed up and raises `ValueError` instead of silently repairing.

Why this exists:
- Preserve user intent and avoid destructive mutation.

Whether it appears intentional:
- Yes.

Why it may be safe:
- Strong integrity posture.

Why it may be risky:
- Low risk.

Whether prod behavior is appropriate:
- Yes.

Recommendation:
- Keep as-is.

### `AUD-001` Quick audit silently degrades parse quality

Code locations:
- [aidlc/audit/quick_engine.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/audit/quick_engine.py:97)

Paraphrase:
- Many parse/read failures only increment degraded counters via `_mark_degraded(...)`; they do not emit direct logs here.

Why this exists:
- Quick audit is meant to be resilient and deterministic.

Whether it appears intentional:
- Yes.

Why it may be safe:
- The audit still completes.

Why it may be risky:
- Users may miss that module/framework detection is partial unless they inspect degraded stats.

Whether prod behavior is appropriate:
- Yes, but telemetry is too quiet.

Recommendation:
- Elevate degraded counters into the executive summary and report header.

### `AUD-002` Runtime audit failures are captured, not escalated

Code locations:
- [aidlc/audit/runtime_engine.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/audit/runtime_engine.py:98)

Paraphrase:
- Runtime build/test/e2e command failures become failed tier results with excerpts.

Why this exists:
- Audit mode is observational.

Whether it appears intentional:
- Yes.

Why it may be safe:
- This is the right shape for an audit report.

Why it may be risky:
- Very low.

Whether prod behavior is appropriate:
- Yes.

Recommendation:
- Keep as-is.

### `ACC-002` Account health-check exceptions lose detail

Code locations:
- [aidlc/accounts/manager.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/accounts/manager.py:147)

Paraphrase:
- Health-check exceptions are reduced to warning + `health_status="unknown"`.

Why this exists:
- Avoid breaking account listing/validation flows on provider probe bugs.

Whether it appears intentional:
- Yes.

Why it may be safe:
- Keeps the CLI usable.

Why it may be risky:
- Loses diagnostic specificity.

Whether prod behavior is appropriate:
- Mostly yes.

Recommendation:
- Persist last validation error string in account metadata.

### `RTE-002` `mark_used()` failures are fully suppressed

Code locations:
- [aidlc/routing/engine.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/routing/engine.py:191)

Paraphrase:
- Account last-used update errors are swallowed with `except Exception: pass`.

Why this exists:
- Bookkeeping must not affect prompt execution.

Whether it appears intentional:
- Yes.

Why it may be safe:
- It is non-critical metadata.

Why it may be risky:
- It hides account-state write problems.

Whether prod behavior is appropriate:
- Yes, but too silent.

Recommendation:
- Debug-log the failure.

## Section 4: Categorization

### Acceptable prod notes

- `CLA-001` Claude retry/backoff for transient and outage conditions
- `FIN-002` Best-effort diff/branch metadata in finalization
- `CFG-001` Lint detection warnings
- `CFG-002` Config auto-merge fail-closed behavior
- `AUD-002` Runtime audit failures captured as failed tiers
- `COP-001` Copilot secondary probe suppression

### Acceptable but should be documented

- `OAI-001` OpenAI adapter has weaker retry behavior than Claude
- `ACC-002` Account validation degrades to `unknown`

### Acceptable but needs better telemetry

- `AUD-001` Degraded audit parsing
- `RTE-002` Suppressed `mark_used()` bookkeeping failures
- `FIN-001` Post-finalization config refresh failure
- `CLA-002` Timeout-stop accepted output should be marked degraded

### Should be tightened before prod

- `VAL-001` No-tests validation skip by default
- `IMP-001` Non-strict change detection
- `ACC-001` Empty-account fallback on state corruption
- `RTE-001` Account/routing fallback after swallowed failures

### High risk / hidden failure

- `VAL-001`
- `IMP-001`

### Security-sensitive suppression

- `SEC-001`

### Data loss / corruption risk

- `IMP-001`
- `ACC-001`

### Observability blind spot

- `AUD-001`
- `RTE-002`
- `FIN-001`
- `CLA-002`

## Section 5: Environment Review

Where prod is quieter than non-prod:
- Not much evidence of prod-only log suppression. The code generally logs in all profiles.

Where prod is more permissive than non-prod:
- The opposite is true. `runtime_profile=production` tightens validation and change-detection defaults in [aidlc/config.py](/Users/michaelfuscoletti/Desktop/aidlc/aidlc/config.py:403).

Where prod may fail open:
- If the production profile is not explicitly enabled, default config remains permissive:
  - `strict_validation=False`
  - `validation_allow_no_tests=True`
  - `fail_on_validation_incomplete=False`
  - `strict_change_detection` not forced

Where prod may hide actionable errors:
- Account-state corruption currently downgrades to warning and can change routing behavior.
- Plaintext credential fallback reduces security posture with only one warning.
- Degraded audit parsing can under-report scan incompleteness.

Whether these differences are reasonable:
- The production profile itself is reasonable.
- Relying on callers to opt into the production profile is the weak point.

## Section 6: Recommended Remediation Plan

### Quick wins

- Make `strict_change_detection=True` by default.
- Make `validation_allow_no_tests=False` by default, or convert “skip validation” into degraded run status.
- Add debug or warning logs for suppressed router/account bookkeeping failures.
- Surface degraded audit counters in the top-level audit summary.

### Medium effort cleanup

- Standardize retry policy across providers.
- Persist structured degradation markers in run state for:
  - timeout-stop accepted output
  - config refresh failures
  - routing/account fallback activation
- Backup/quarantine corrupt `accounts.json` instead of silently treating it as empty.

### High value hardening

- Require explicit opt-in for plaintext credential storage.
- Fail routed runs closed when account metadata is unreadable.
- Require explicit override for no-test repositories in autonomous edit mode.

### Documentation gaps

- Document provider retry asymmetry.
- Document that production-grade runs should use `runtime_profile=production`.
- Document exact degraded-mode semantics for audit parsing and routing fallback.

### Test gaps

- Add tests asserting that missing tests fail validation under default hardened config.
- Add tests asserting that implementation success is rejected when change detection is unavailable.
- Add tests asserting account-state corruption blocks routed execution.
- Add tests for degraded markers when timeout-stop output is accepted.

### Telemetry / alerting gaps

- Counter for routing fallback activations by phase/provider.
- Counter for degraded audit parse events.
- Counter for config refresh failures.
- Counter for accepted-partial provider outputs.

## Direct Verdict

Prod posture has notable risk areas.

Why:
- The code generally logs and bounds failures well, but a few permissive defaults still allow weak verification and degraded state to present as normal progress. Those should be tightened so production-path suppressions are overwhelmingly `Note` or `Low`.
