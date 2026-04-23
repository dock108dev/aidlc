# Roadmap

This roadmap is the issue list for the prior remediation effort. The
**core-focus audit** (post-ISSUE-013) supersedes much of the lifecycle/UX
work below: `aidlc plan`, `aidlc improve`, `aidlc audit`, `aidlc finalize`,
and `aidlc validate` were removed; the auditor stopped writing
`BRAINDUMP.md`/`ARCHITECTURE.md`; and the vague `ssot`/`security`/`abend`
finalization passes were dropped. See `CHANGELOG.md` for the full list. The
issues below are kept for historical reference.

## Current effort

### ISSUE-001 — Foundation docs pass
Produce / update `ARCHITECTURE.md`, `ROADMAP.md` (this file), `DESIGN.md`,
`CHANGELOG.md` at the repo root; new `docs/MIGRATION-existing-projects.md`;
update `docs/configuration.md` (fix flat-key documentation that doesn't match
DEFAULTS) and `docs/cli-lifecycle.md` (document `aidlc reset`, new early-stop
behavior).
**Acceptance:** all six files present; DESIGN.md sections map 1:1 to
ISSUE-002..ISSUE-013; configuration.md no longer mentions flat
`claude_model_planning` keys (which DEFAULTS does not define).

---

### Tier A — Smoking-gun bugs

### ISSUE-002 — Fix doc-overwrite bug in `plan_session.py`
The wizard runs Claude with `allow_edits=True`, Claude writes the full doc to
the project root via Write, then `_save_drafts` overwrites that file with the
chat-summary stdout (e.g., *"ARCHITECTURE.md has been written to the project
root..."*).
**Fix:** switch `_generate_drafts` to `allow_edits=False` so `result["output"]`
contains the doc body. `_save_drafts` becomes the single writer.
**Acceptance:** new unit test mocks `cli.execute_prompt` returning a known
body; assert `(project_root/"ARCHITECTURE.md").read_text() == body`. Smoke
test of `aidlc plan --wizard` against a tmp project produces full docs at
project root, not the chat-summary stub.

### ISSUE-003 — Fix model-override precedence bug
DEFAULTS hardcodes `providers.claude.phase_models.{planning, implementation,
…}: "sonnet"`. The router checks `phase_models[phase]` first, so a user
setting `providers.claude.default_model: "opus"` has no effect — phase_models
still wins.
**Fix:** in `routing/context.py:resolve_model_for_phase`, treat user-set
`default_model` as a per-provider override of the DEFAULT phase_models.
Precedence becomes: user `phase_models[phase]` → user `default_model` →
DEFAULT `phase_models[phase]` → DEFAULT `default_model` → adapter default.
**Acceptance:** unit test loads a config with only
`providers.claude.default_model: "opus"`, calls
`resolve_model_for_phase("planning", …)`, asserts `"opus"`. The router's
debug log line names the source of the chosen model.

### ISSUE-004 — Within-provider model fallback on token exhaustion
When sonnet returns `out of tokens`, the engine excludes the entire **provider**
and tries the next *provider*. With Claude as the only enabled provider, the
loop exits without trying opus/haiku.
**Fix:** add `providers.<id>.model_fallback_chain: ["sonnet", "opus", "haiku"]`
config. In the engine's exhaustion branch, walk the chain on the same provider
before excluding it. New `is_model_exhausted_result` signal distinguishes
per-model from per-provider exhaustion.
**Acceptance:** integration test stubs `execute_prompt` to return
`token_exhausted` for `sonnet`; asserts the next call uses `opus`; asserts the
run continues if `opus` succeeds. The "Stopping run" log line shows the actual
chain attempted.

---

### Tier B — Loop-rewrite quality

### ISSUE-005 — Thread prior issues into planner prompt
`existing_issues` (loaded from prior `.aidlc/issues/`) is currently only used
for ID-collision avoidance. The planner prompt has no knowledge of prior work.
**Fix:** extend `_render_existing_issues_section` to render prior issues with
status (verified / implemented / failed / pending) and a one-line
implementation-notes summary, under `## Prior Run — Already Done (do not
redo)`. Update the planning system prompt: *"If a gap is already addressed by
a prior verified/implemented issue, do not re-create — focus on deltas."*
Drop priority: 1st under prompt-budget pressure (so it cannot starve the
schema/instructions section).
**Acceptance:** end-to-end with a project that has a populated
`.aidlc/issues/` dir; `aidlc plan --dry-run` produces a captured prompt
containing the prior-issues section.

### ISSUE-006 — Thread foundation docs into planner prompt
The planner builds `planning_index.md` but doesn't include the actual
ROADMAP / ARCHITECTURE / DESIGN content in the prompt with a
"this is committed; respect it" framing.
**Fix:** new `## Foundation Docs (committed — incremental changes only)`
section in `build_prompt` rendering the first ~2k chars of each. Update the
planning system prompt: *"The roadmap/architecture/design are authoritative.
Propose issues only inside their scope. If a fundamental direction change is
needed, propose a single 'Update foundation docs' issue rather than diverging
silently."*
Drop priority: 3rd under budget pressure.
**Acceptance:** plan against a repo with explicit `ARCHITECTURE.md`; assert
the prompt contains a substring of that doc.

### ISSUE-007 — Implementer prompt: preserve, don't rewrite
The current `Must not:` clause is loose. Issues that overlap working systems
get treated as redesign permission.
**Fix:** add to the implementation prompt:
- *"If a file or system already exists and works (has tests, has callers),
  your default is to modify in place and preserve the public surface.
  Rewriting is a last resort and requires updating every caller in the same
  change."*
- *"Before editing a system, list its existing tests in your output. Breaking
  a test outside your acceptance criteria is a regression, not progress."*
- Add `"existing_callers_checked": [<file:line>]` to the JSON output schema.
**Acceptance:** unit test on the prompt builder asserts the new clauses are
present.

---

### Tier C — Lifecycle / UX

### ISSUE-008 — `aidlc reset` command
No CLI affordance to clear stale state. Users must `rm -rf .aidlc/`.
**Fix:** new subcommand. Default deletes `runs/`, `reports/`, `issues/`,
`session/`, `audit_result.json`, `planning_index.md`. Preserves `config.json`
(auth/provider settings). Flags: `--all` (also delete config.json with
confirmation), `--dry-run`, `--keep-issues`, `--yes`/`-y`.
**Acceptance:** integration test in a tmpdir with a fake `.aidlc/` tree;
verify the right files survive each flag combination.

### ISSUE-009 — Don't auto-run finalization on early stop
When `stop_reason` is set with work remaining, the implementer auto-runs
`ssot`/`abend`/`cleanup` finalization passes — burning more budget at exactly
the moment we want to stop cleanly.
**Fix:** gate the auto-finalize on a new config flag
`implementation_finalize_on_early_stop` (default `false`). Always log a clear
single-line summary of why it stopped and how to resume.
**Acceptance:** simulate `stop_reason = "out of tokens"`; with the new
default, assert `Finalizer.run` is NOT called.

### ISSUE-010 — Mark abandoned runs (signal handler + resume detection)
A run killed externally leaves `status = running`. There's no signal to
differentiate from a still-active run, and resume can't tell crashed runs from
live ones.
**Fix:** register `atexit` + `SIGINT`/`SIGTERM` handlers in `runner.py` that
flip `status = interrupted` on non-clean exit. On resume, any
`running`/`interrupted` run older than 1 hour is surfaced as `abandoned`.
`aidlc status` displays the abandoned state.
**Acceptance:** write a state with `status=running, last_updated=2h_ago`;
call resume logic; assert it surfaces the abandoned run.

### ISSUE-011 — Adaptive diminishing-returns threshold
A fixed 3-cycle empty-cycle threshold can force-exit planning on large repos
in the middle of work.
**Fix:** new config keys
`planning_diminishing_returns_min_threshold` (default 3) and
`planning_diminishing_returns_max_threshold` (default 6). Effective threshold
= `clamp(min, ceil(num_issues_so_far / 10), max)`. Log adaptive value when
threshold is hit.
**Acceptance:** unit test calls the threshold function with varying issue
counts and asserts the clamp.

### ISSUE-012 — Failed-issue retry policy + `--retry-failed` flag
A failed issue stays `IssueStatus.FAILED` forever, even when the cause was a
transient token exhaustion.
**Fix:** distinguish failure causes (`failed_token_exhausted`,
`failed_dependency`, `failed_test_regression`, `failed_unknown`). On a fresh
implementation cycle (or `aidlc run --retry-failed`), reopen issues whose
cause is in `{token_exhausted, unknown}` (transient). Leave `dependency` and
`test_regression` for manual review.
**Acceptance:** integration test creates a state with a failed-token-exhausted
issue, runs with `--retry-failed`, asserts the issue is `pending`.

---

### Tier D — Hygiene

### ISSUE-013 — Session-dir pruning + doc-gap scan caching
Two small wins:
1. Session pruning: keep most recent N session subdirs under `.aidlc/session/`
   (config: `session_dir_max_keep: 10`); prune the rest at start of each
   `aidlc plan`.
2. Doc-gap scan caching: `detect_doc_gaps` re-scans the entire repo every
   planning cycle. Cache the result keyed on a hash of all doc mtimes;
   invalidate when any doc changes.
**Acceptance:** integration test runs `aidlc plan --dry-run` twice on the
same repo; asserts `detect_doc_gaps` is called once (cache hit on 2nd cycle).

---

## Beyond this effort (deferred)

- The BRAINDUMP "Agent Audit / Training Console" — separate v2 effort. Treat
  the artifacts produced by ISSUE-013 (clean session dirs, audit trail) as
  prerequisites.
- Provider account hot-rotation when one account's tier limit is hit but
  another account on the same provider has capacity (the routing code
  partially supports this; CLI surface and config schema are not yet wired
  through).
- Per-issue cache-read budget cap (the audited run had 8M cache_read tokens on
  a single issue; needs investigation in `context_prep.py`).
