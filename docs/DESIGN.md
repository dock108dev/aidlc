# Design

Per-fix design notes for the prior remediation effort
(ISSUE-001..ISSUE-013, all closed). Format per section:
**Problem** → **Approach** → **Alternatives considered** → **Touchpoints** →
**Tests**.

> **Historical record.** This document captures the design rationale for
> a closed remediation effort. Some sections describe behavior that has
> since been further changed by the core-focus audit and subsequent SSOT
> cleanup:
> - ISSUE-002 and ISSUE-013 describe behavior in the now-retired
>   `aidlc plan` wizard / `plan_session.py`.
> - ISSUE-009's "automatic ssot/abend/cleanup on early stop" refers to
>   passes that have since been removed; the early-stop opt-in now runs
>   `cleanup` only.
> - ISSUE-011's "deprecated `diminishing_returns_threshold` retained as
>   fallback" no longer holds — that key was removed entirely in the SSOT
>   cleanup pass.
>
> The notes are kept here as engineering context for anyone re-touching
> the same code paths. See [CHANGELOG.md](CHANGELOG.md) for the canonical
> change history.

---

## ISSUE-002 — Doc-overwrite bug fix

**Problem.** `aidlc/plan_session.py:283` runs Claude with `allow_edits=True`.
Claude writes the full doc body via its Write tool, returning a chat-style
summary as stdout (e.g., *"ARCHITECTURE.md has been written to the project
root..."*). At line 286 that summary is captured as the draft. At line 308
`doc_path.write_text(content)` overwrites the project-root file with the
summary — wiping the full content Claude just wrote. The audit trail at
`.aidlc/session/<ts>/<doc>.generated` then shows the summary, not the body,
adding to the confusion.

**Approach.** Switch `_generate_drafts` to `allow_edits=False`. With edits
disabled, Claude returns the doc body as text in stdout. `_save_drafts`
becomes the single writer that puts the text at the project root and records
the `.generated` audit copy. The research path at lines 214-237 already uses
this pattern (and wraps the output in a header).

**Alternatives considered.**
- Keep `allow_edits=True` and have `_save_drafts` skip writes when the file
  was modified during the call (mtime check). Rejected: fragile, depends on
  CLI behavior we don't control.
- Detect "Claude wrote the file" by sniffing the stdout for the
  "has been written" pattern. Rejected: locale/wording-dependent, brittle.

**Touchpoints.**
- `aidlc/plan_session.py:283` — `allow_edits=True` → `allow_edits=False`.
- `aidlc/plan_session.py:292-311` — `_save_drafts` is unchanged.
- `aidlc/claude_cli.py` — `execute_prompt(allow_edits=False)` already returns
  the response body in `result["output"]`. No change needed.

**Tests.**
- `tests/plan_session/test_save_drafts.py` — mock `cli.execute_prompt` to
  return a known body; assert the project-root file equals the body.
- Smoke: run `aidlc plan --wizard` against a tmp project; assert the
  generated `ARCHITECTURE.md` is > 2 KB and does not start with the summary
  pattern.

---

## ISSUE-003 — Model-override precedence

**Problem.** `aidlc/config.py` DEFAULTS pin `providers.claude.phase_models` to
`{planning: "sonnet", implementation: "sonnet", …}`. The router in
`aidlc/routing/context.py:resolve_model_for_phase` checks
`phase_models[phase]` first, then `default_model`. A user setting
`providers.claude.default_model: "opus"` in `.aidlc/config.json` therefore has
no effect — `phase_models.planning` (still "sonnet" from DEFAULTS) wins.

Worse: the wizard-generated `.aidlc/config.json` from
`config.py:write_default_config` doesn't include a `phase_models` block at
all, so users have no idea it exists or that they need to override it.

**Approach.** In `resolve_model_for_phase`, treat user-set `default_model` as
a per-provider override that wins over DEFAULT phase_models. Track whether
each value was user-set (we already do this via `user_keys` in `load_config`).
New precedence:

```
user phase_models[phase]   → use it
user default_model         → use it (overrides DEFAULT phase_models)
DEFAULT phase_models[phase] → use it
DEFAULT default_model      → use it
adapter default            → use it
```

To implement, `load_config` flags user-set provider entries by storing the
raw user dict at `config["_user_provider_overrides"]` (internal key, won't
serialize). `resolve_model_for_phase` consults that map.

Add a debug log on first resolve per phase: `Resolved model for planning: opus
(source: user default_model override)` so users can confirm what's happening
without reading code.

**Alternatives considered.**
- Strip DEFAULT `phase_models` and rely solely on `default_model`. Rejected:
  breaks projects relying on per-phase model selection (e.g., cheap planning,
  expensive implementation_complex).
- Add a wizard prompt asking users to override per-phase. Rejected: too much
  friction; most users want one model unless they say otherwise.

**Touchpoints.**
- `aidlc/config.py:_merge_user_config` — store user-set provider entries on
  `config["_user_provider_overrides"]` for later precedence checks.
- `aidlc/routing/context.py:226-245` — `resolve_model_for_phase` consults the
  override map.
- `aidlc/routing/engine.py` — log the chosen model + source on first resolve
  per phase.

**Tests.**
- `tests/routing/test_model_precedence.py` — table-driven test with multiple
  config shapes, asserts the resolved model.
- `tests/routing/test_log_source.py` — captures router debug log; asserts the
  `(source: …)` annotation is correct.

---

## ISSUE-004 — Within-provider model fallback

**Problem.** `aidlc/routing/engine.py:197-204` excludes the entire
**provider** when a model returns `out of tokens`. With Claude as the only
enabled provider, the loop exits without trying `opus` or `haiku`. Users with
single-provider setups have no fallback.

**Approach.** Two pieces:

1. **Config:** add `providers.<id>.model_fallback_chain: ["sonnet", "opus",
   "haiku"]` to DEFAULTS. Per-provider chains so non-Claude providers get
   sensible defaults too. Empty/missing chain = current behavior (provider
   exclusion).

2. **Engine retry loop:** when a result trips
   `is_token_exhaustion_result`, before excluding the provider:
   - Look up the provider's chain.
   - Find the current model's index, advance to the next entry not in
     `excluded_models` for this provider.
   - Add the current `(provider, model)` to `excluded_models`.
   - Re-resolve with `model_override=<next_chain_entry>`.
   - If the chain is exhausted (next entry None or all excluded), then fall
     through to the existing provider-exclusion path.

3. **New signal:** `is_model_exhausted_result(result) -> bool` in
   `result_signals.py`. Distinguishes per-model from per-provider exhaustion
   by inspecting the error text — Claude CLI typically names the model in
   quota messages (e.g., `claude-sonnet-4-5 has reached its quota`). If the
   message names a model, treat as per-model; otherwise per-provider. This
   keeps the engine code clean: the signal makes the decision.

**Alternatives considered.**
- Round-robin all enabled models. Rejected: surprising behavior; cost
  unpredictable. Chain order is explicit and predictable.
- Try alternate models *before* exhaustion (preemptive load-balancing).
  Rejected: out of scope; this fix targets the user's specific complaint.

**Touchpoints.**
- `aidlc/config.py` DEFAULTS — add `model_fallback_chain` to each provider:
  - claude: `["sonnet", "opus", "haiku"]`
  - openai: `["gpt-5.4", "gpt-5.4-mini"]`
  - copilot: `[]` (let it use whatever)
- `aidlc/routing/result_signals.py` — new `is_model_exhausted_result`.
- `aidlc/routing/engine.py:189-249` — exhaustion branch consults chain.
- `aidlc/routing/engine.py` — "Stopping run" log includes the chain attempted.

**Tests.**
- `tests/routing/test_fallback_chain.py` — stub `execute_prompt` to return
  exhausted-for-sonnet then success-for-opus; assert two CLI calls, the
  second using `model_override="opus"`.
- `tests/routing/test_chain_exhausted.py` — all chain entries exhausted;
  assert provider exclusion kicks in.
- `tests/routing/test_signal_per_model.py` — `is_model_exhausted_result`
  table-driven test.

---

## ISSUE-005 — Thread prior issues into planner prompt

**Problem.** `aidlc/planner.py:316-318` only uses `existing_issues` (loaded
from `.aidlc/issues/*.md`) for ID-collision avoidance. The planner prompt
in `planner_helpers.py:build_prompt` only renders issues from the
**current run's state**, so on a re-run the planner has no idea what was
done before and re-plans from scratch. Symptom: a working physics module gets
re-issued and the implementer rewrites it.

**Approach.** Extend `_render_existing_issues_section(planner)` to take an
optional `prior_issues` argument with the same shape as
`planner.state.issues`. Render them as a separate section labeled
`## Prior Run — Already Done (do not redo)`, including each issue's
`status` (verified / implemented / failed / pending) and a one-line
implementation-notes excerpt (≤ 80 chars).

Update the planning system prompt (where instructions are defined — likely
`planner_helpers.py` or `aidlc/configs/`): *"Prior-run issues marked
verified or implemented represent committed work. Do not re-create them.
Focus on deltas: gaps in coverage, regressions revealed since they shipped,
or follow-on work documented in their notes."*

For prompt-budget pressure (`_enforce_prompt_budget`), the prior-issues
section is **first** to be dropped. The trade-off: if we hit budget, the
schema and instructions stay intact, and Claude reverts to the older behavior
of re-planning. That's a degradation but not a crash.

**Alternatives considered.**
- Inject prior issues directly into the system prompt instead of the volatile
  body. Rejected: defeats prompt caching (system prompt is part of the static
  prefix).
- Pre-filter prior issues to only "verified" ones. Rejected: failed/pending
  issues from prior runs are useful context too — they tell the planner what
  was attempted and why it didn't land.

**Touchpoints.**
- `aidlc/planner_helpers.py:25-130` — extend
  `_render_existing_issues_section`.
- `aidlc/planner_helpers.py:300-358` — wire `planner.existing_issues` through
  in `build_prompt`.
- `aidlc/planner_helpers.py:133-163` — `_enforce_prompt_budget` drops the
  new section first.
- Planning system prompt content (find via grep for "create_issue" in
  `planner_helpers.py` or `aidlc/configs/`).

**Tests.**
- `tests/planner/test_prior_issues_in_prompt.py` — construct a planner with
  populated `existing_issues`; assert `build_prompt` output contains the
  expected section + "do not redo" framing.
- `tests/planner/test_budget_drops_prior_first.py` — budget-constrained
  prompt drops prior-issues before cycle notes or foundation docs.

---

## ISSUE-006 — Thread foundation docs into planner prompt

**Problem.** Same root cause as ISSUE-005 but for `ROADMAP.md`,
`ARCHITECTURE.md`, `DESIGN.md`. The planner builds `planning_index.md` (an
index of these docs) but doesn't include their actual *content* in the prompt
with a "this is committed" framing.

**Approach.** New `## Foundation Docs (committed — incremental changes only)`
section in `build_prompt`. Render the first ~2 KB of each foundation doc
present at the project root, with a pointer to the full file
(`Read .aidlc/planning_index.md and the linked file for full context`).

Update the planning system prompt: *"The roadmap, architecture, and design
docs are authoritative. Propose issues only inside their scope. If a
fundamental direction change is needed, propose a single 'Update foundation
docs' issue rather than diverging silently."*

Drop priority under budget pressure: 3rd (after prior-issues, then prior
cycle notes).

**Alternatives considered.**
- Include the full doc content. Rejected: blows past planning prompt budget
  on real projects (foundation docs are 5-15 KB each).
- Summarize each doc with a separate Claude call per cycle. Rejected: too
  many CLI calls per cycle, defeats caching.

**Touchpoints.**
- `aidlc/planner_helpers.py:300-358` — new section in `build_prompt`.
- `aidlc/scanner.py` — already loads doc files; we read from
  `planner.doc_files` if available.
- `aidlc/planner_helpers.py:133-163` — drop priority registered.

**Tests.**
- `tests/planner/test_foundation_docs_in_prompt.py` — set up a project with
  `ARCHITECTURE.md`; plan; assert a substring of that doc is in the prompt.

---

## ISSUE-007 — Implementer prompt: preserve, don't rewrite

**Problem.** `aidlc/implementer_helpers.py:80` says *"Must not: Touch
unrelated files; break existing behavior; leave dead code."* "Break existing
behavior" is the only check, and it's loose. An issue like *"improve hole
physics"* against an existing physics module gets treated as redesign
permission.

**Approach.** Add to the implementation prompt:

> If a file or system already exists and works (it has tests, has callers),
> your default is to **modify in place** and preserve the public surface.
> Rewriting is a last resort and requires you to also update every caller in
> the same change.
>
> Before editing a system, list its existing tests in your output. If you
> break a test that's not in your acceptance criteria, that's a regression,
> not progress.

Add to the JSON output schema: `"existing_callers_checked": [<file:line>, …]`.
This makes the requirement observable; future post-implementation passes can
warn when this list is empty for a non-trivial change.

**Alternatives considered.**
- Hard-block file rewrites with a tool restriction. Rejected: too brittle;
  legitimate rewrites exist (e.g., when an issue's acceptance criteria call
  for it).
- Have the implementer agent read each acceptance criterion as a constraint.
  Already done; this issue tightens the surrounding framing.

**Touchpoints.**
- `aidlc/implementer_helpers.py:80-160` — prompt builder.
- `aidlc/implementer.py` — JSON parser accepts the new optional field;
  doesn't fail if absent (backward-compatible).

**Tests.**
- `tests/implementer/test_prompt_preserves_clauses.py` — assert new clauses
  are in the built prompt.
- `tests/implementer/test_json_parses_new_field.py` — JSON parser handles the
  new field's presence and absence.

---

## ISSUE-008 — `aidlc reset` command

**Problem.** No CLI affordance to clear stale state. `cli_commands.py:99`
literally says *"delete .aidlc/ to start fresh"* — i.e., `rm -rf` it
yourself. Users with auth set up would lose `config.json`.

**Approach.** New `aidlc reset` subcommand. Default deletion list:
`runs/`, `reports/`, `issues/`, `session/`, `audit_result.json`,
`planning_index.md`, plus any top-level `.aidlc/CONFLICTS.md` or
`.aidlc/run.lock`. **Preserve** `.aidlc/config.json`.

Flags:
- `--all`: also delete `config.json` (with explicit confirmation prompt).
- `--dry-run`: print what would be deleted, delete nothing.
- `--keep-issues`: preserve `issues/`.
- `--yes` / `-y`: skip confirmation prompt.

Confirmation prompt by default. Output mirrors `aidlc init`'s style.

**Alternatives considered.**
- Make this a flag on `aidlc init` (`aidlc init --reset`). Rejected: confusing
  semantics; `init` should be additive.
- Bundle into `aidlc run --fresh`. Rejected: a destructive action shouldn't
  be a flag on the most-used command; explicit subcommand is clearer.

**Touchpoints.**
- `aidlc/cli_parser.py` — add the subparser.
- `aidlc/cli_commands.py` — new `cmd_reset` handler.
- `aidlc/__main__.py` — wire dispatch.

**Tests.**
- `tests/cli/test_reset.py` — populate a tmp `.aidlc/` tree; run reset with
  each flag combination; assert the survivor set.

---

## ISSUE-009 — Don't auto-run finalization on early stop

**Problem.** `aidlc/implementer.py:286-305` automatically runs `ssot`,
`abend`, `cleanup` finalization passes when implementation stops early with
work remaining. This burns more budget at exactly the moment we want to stop
cleanly. The user has no opt-out.

**Approach.** Gate the auto-finalize on a new config flag
`implementation_finalize_on_early_stop` (default **false**). On early stop:
- Always log a clear single-line `STOP REASON: <reason>` plus a single-line
  `RESUME WITH: aidlc run --resume` (or whichever instruction is appropriate).
- If the flag is true, run the existing passes; otherwise skip.

The single-line stop reason is the user's main observability into what
happened. Today it's buried in mixed log output; we promote it to a
visually-distinct line at the end of the run.

**Alternatives considered.**
- Run only one cheap pass (`abend`) by default. Rejected: still burns budget
  at the wrong moment; opt-in is cleaner.
- Make this a CLI flag on `aidlc run`. Possible follow-up; the config flag is
  the canonical place since it should also affect resumed runs.

**Touchpoints.**
- `aidlc/config.py` DEFAULTS — add
  `implementation_finalize_on_early_stop: false`.
- `aidlc/implementer.py:286-305` — gate the block on the new flag; promote
  the stop-reason log to a visually-distinct line.

**Tests.**
- `tests/implementer/test_early_stop_no_finalize.py` — set
  `stop_reason = "out of tokens"`; with flag false, assert
  `Finalizer.run` is not called.

---

## ISSUE-010 — Mark abandoned runs

**Problem.** A run killed externally (Ctrl-C, OOM, SIGTERM) leaves
`status = running`, indistinguishable from a still-active run. On resume the
user can't tell whether the prior run finished or crashed.

**Approach.** Two pieces:

1. **Signal handlers** in `runner.py`: register `atexit.register` plus
   `signal.signal(SIGINT, …)` and `signal.signal(SIGTERM, …)`. On non-clean
   exit, if `state.status == running`, flip it to `interrupted` and save.
   Use a guard so handlers don't fire on normal completion.

2. **Resume-time detection** in `state_manager.py` and `runner.py`: on
   `aidlc run --resume`, find the latest run; if its status is in
   `{running, interrupted}` and `last_updated` is older than 1 hour, mark it
   `abandoned`. Surface in the resume prompt: *"Latest run shows as abandoned
   (idle for 2.5h, last phase: implementing). Resume it, or start fresh?"*

3. `aidlc status` shows abandoned runs with a yellow ABANDONED badge.

**Alternatives considered.**
- Heartbeat file. Rejected: more state to maintain; the `last_updated`
  timestamp on `state.json` is sufficient.
- Detect abandonment via PID. Rejected: PIDs get reused; not portable.

**Touchpoints.**
- `aidlc/runner.py` — register handlers at runner start, deregister on clean
  exit.
- `aidlc/state_manager.py` — add `RunStatus.INTERRUPTED` and
  `RunStatus.ABANDONED` enum members; `find_latest_run` and resume logic
  consult `last_updated`.
- `aidlc/cli_commands.py:cmd_status` — render new statuses.

**Tests.**
- `tests/runner/test_signal_handler.py` — synthetic SIGINT in a subprocess;
  assert the resulting state is `interrupted`.
- `tests/state_manager/test_abandoned_detection.py` — write a state with
  `status=running, last_updated=2h_ago`; resume detection marks it
  `abandoned`.

---

## ISSUE-011 — Adaptive diminishing-returns threshold

**Problem.** `planner.py` previously exited planning after a fixed
`diminishing_returns_threshold` consecutive empty cycles. On a large repo
with many issues already drafted, a stall in the middle of work could cross
the threshold and force-exit even when scope remained.

**Approach.** Config keys (SSOT):
- `planning_diminishing_returns_min_threshold: 3`
- `planning_diminishing_returns_max_threshold: 6`

Effective threshold = `clamp(min, ceil(num_issues_so_far / 10), max)`. So:

| Issues so far | Threshold |
|---|---|
| ≤ 30 | 3 |
| 31-40 | 4 |
| 41-50 | 5 |
| ≥ 51 | 6 |

When the threshold is hit, log: `Diminishing returns: <N> empty cycles out of
<T> required (adaptive: <T> from <N_issues> issues); finalizing planning.`

**Alternatives considered.**
- Always 5. Rejected: too patient on small projects; planning takes too long.
- Issue-count-driven minimum window for the rolling check. Possible follow-on.

**Touchpoints.**
- `aidlc/config.py` DEFAULTS — only the new keys are read. The legacy
  `diminishing_returns_threshold` config key has been fully removed (no
  read, no deprecation log).
- `aidlc/planner.py` — compute effective threshold each cycle.

**Tests.**
- `tests/planner/test_adaptive_threshold.py` — table-driven over issue
  counts.

---

## ISSUE-012 — Failed-issue retry policy

**Problem.** `aidlc/implementer.py:355-380` marks failed issues
`IssueStatus.FAILED` with no cause distinguished. Once failed, an issue is
silently skipped on subsequent cycles. The user has no way to ask "retry
those that failed for transient reasons" without manually editing
`.aidlc/issues/<id>.md`.

**Approach.** Two pieces:

1. **Cause distinction**: when marking failed, set
   `issue.failure_cause` to one of:
   - `failed_token_exhausted` (transient — provider quota)
   - `failed_dependency` (other issue blocked us)
   - `failed_test_regression` (we broke tests we shouldn't have)
   - `failed_unknown` (catch-all)

2. **Auto-reopen on fresh cycle / `--retry-failed` flag**: at the top of
   `implementer.run()`, scan failed issues; reopen those with
   `failure_cause in {token_exhausted, unknown}` to `pending`. Leave
   `dependency` and `test_regression` for manual review.

   New CLI flag `aidlc run --retry-failed`: forces all failed issues
   reopened, regardless of cause.

**Alternatives considered.**
- Auto-retry on every cycle. Rejected: tight loop on real failures.
- Retry-with-backoff (try, wait, try). Rejected: out of scope; user explicitly
  driving via `--retry-failed` is sufficient.

**Touchpoints.**
- `aidlc/issue_model.py` — add `failure_cause` field (Optional[str], default
  None for backward compat).
- `aidlc/implementer.py:355-380` — set cause when marking failed.
- `aidlc/implementer.py:155-180` — reopen-on-start logic.
- `aidlc/cli_parser.py` — add `--retry-failed` flag to `run` subparser.
- `aidlc/cli_commands.py:cmd_run` — pass through to runner.

**Tests.**
- `tests/implementer/test_failure_cause.py` — assert cause is set correctly
  per failure type.
- `tests/implementer/test_reopen_transient.py` — populate state with mixed
  failed issues; assert only transient ones reopen.
- `tests/cli/test_retry_failed_flag.py` — flag forces all reopen.

---

## ISSUE-013 — Session pruning + doc-gap caching

**Problem.**
1. `aidlc/plan_session.py:296` makes a new timestamped subdir under
   `.aidlc/session/` per `aidlc plan` invocation. No pruning. Disk grows
   unbounded; stale dirs carry confusing artifacts.
2. `aidlc/runner.py:277` calls `detect_doc_gaps` on every planning cycle,
   re-scanning the entire repo. Doc content rarely changes during a run; we
   waste time and tokens.

**Approach.**

1. **Session pruning.** New config `session_dir_max_keep: 10`. At start of
   `aidlc plan` (and start of `aidlc run` planning phase), enumerate
   `.aidlc/session/<ts>/` dirs sorted by mtime, delete the oldest beyond
   `max_keep`. Always keep at least the current run's dir.

2. **Doc-gap caching.** In `runner.py:_run_planning_phase` (or wherever
   `detect_doc_gaps` is called), compute a cache key = SHA-256 of `(path,
   mtime, size)` tuples for every doc the gap detector reads. Store the
   computed gaps and the key on the runner instance (or the state). On the
   next call, recompute the key; if unchanged, return cached gaps.

   Only invalidate within a single run; subsequent runs always recompute (no
   cross-run cache, to stay correct after external doc edits).

**Alternatives considered.**
- Per-cycle `mtime` check on individual docs. Rejected: more bookkeeping;
  the global hash is simple and adequate.
- Disk-cache the gap result. Rejected: cross-run staleness risk not worth the
  marginal saving.

**Touchpoints.**
- `aidlc/config.py` DEFAULTS — `session_dir_max_keep: 10`.
- `aidlc/plan_session.py:67` and `:292` — prune before creating new session
  dir.
- `aidlc/runner.py:277` — cache wrapper around `detect_doc_gaps`.
- `aidlc/doc_gap_detector.py` — expose the input file list (so the cache key
  can include all docs read).

**Tests.**
- `tests/plan_session/test_session_pruning.py` — populate >max_keep dirs;
  prune; assert the right N survive.
- `tests/runner/test_doc_gap_cache.py` — call planning phase twice;
  assert `detect_doc_gaps` invoked once.

---

## Cross-cutting concerns

**Backward compatibility.**
- ISSUE-009 changes a default (auto-finalize off). Documented in
  `CHANGELOG.md`; users who relied on the old behavior set the new flag true.
- ISSUE-011 removes `diminishing_returns_threshold` entirely; the SSOT keys
  are `planning_diminishing_returns_min_threshold` / `_max_threshold`.
- ISSUE-012 adds an optional field to issue models; existing issues without
  it work unchanged.

**Telemetry / observability.**
- ISSUE-003 adds router debug log line for chosen model + source.
- ISSUE-004 includes the attempted chain in the "Stopping run" log.
- ISSUE-009 promotes the stop reason to a distinct visual line.
- ISSUE-010 surfaces abandoned runs in `aidlc status`.
- ISSUE-011 logs the adaptive threshold computation when it triggers exit.

These are how the user can tell, at a glance, what happened in a run without
reading code.

**Test posture.**
- New tests live next to the modules they exercise: `tests/routing/`,
  `tests/planner/`, `tests/implementer/`, `tests/cli/`.
- Tests for prompt construction assert *substrings* (e.g., "do not redo"),
  not exact strings, to avoid brittleness when wording changes.
- Tests for the engine retry loop use a stub provider that records every
  call so we can assert the sequence of `(provider, model, attempt)` tuples.
