# Changelog

Format: short entry per issue, grouped by tier. See [DESIGN.md](DESIGN.md)
for per-fix rationale.

## Unreleased

### Removed (verify-mode planner pivot)

- **Diminishing-returns multi-empty-cycle wait removed.** The planner used
  to track recent cycles and wait for N consecutive empty cycles before
  offering completion. Replaced by a one-shot **verify cycle**: the first
  cycle that produces 0 new issues switches the next cycle into verify
  mode (a coverage-check prompt that walks BRAINDUMP + discovery findings
  + research + existing issues). If verify is also empty, planning ends;
  if verify surfaces gaps, those issues are filed and the next empty
  cycle ends planning directly without re-verifying. The config keys
  `diminishing_returns_window`, `planning_diminishing_returns_min_threshold`,
  `planning_diminishing_returns_max_threshold`, and the legacy
  `diminishing_returns_threshold` are gone — present-but-set keys in
  user configs are silently ignored. The `_offer_completion` flag, the
  `_adaptive_diminishing_threshold` method, and the
  `COMPLETION_OFFER_INSTRUCTIONS` prompt block are gone too.

- **Wall-clock kill for the Claude CLI removed entirely.** The legacy
  `claude_hard_timeout_seconds` interrupted productive multi-hour
  streaming sessions and produced partial JSON that downstream parsers
  couldn't handle. Activity-based stall detection (`claude_stall_warn_seconds`,
  `claude_stall_kill_seconds`) is the only kill path now. Non-streaming
  provider CLIs (Copilot, OpenAI Codex) read `provider_call_timeout_seconds`
  for their request timeout instead.

### Removed (core-focus audit)

The lifecycle had drifted: a half-dozen subcommands competed with `aidlc run`
and the auditor was overwriting `BRAINDUMP.md`, inverting the design where the
customer's voice is the single source of truth. This pass cuts the surface
back to the core flow.

- **Auditor BRAINDUMP/ARCHITECTURE generation removed.** The auditor is now
  read-only for user-owned docs. It writes `STATUS.md` (a generated artifact)
  and `.aidlc/audit_result.json`, and never touches `BRAINDUMP.md` or
  `ARCHITECTURE.md`. Config knobs `audit_braindump_enabled`,
  `audit_braindump_path`, `audit_planning_workload_stop_ratio`,
  `audit_research_estimate_default_hours`, `audit_issue_estimate_defaults`,
  `audit_include_deferred_backlog` are gone. `AuditResult.braindump_summary`
  is gone. ~290 lines removed from `aidlc/audit/output_engine.py`.
- **`aidlc improve` removed.** It re-implemented the full lifecycle in
  "scoped" form with ~440 lines of duplicated orchestration. To focus a run
  on a specific concern, write it into `BRAINDUMP.md`.
- **`aidlc plan` removed.** The interactive multi-doc generator
  (`plan_session.py`, `plan_wizard.py`, `plan_templates.py` — ~920 lines)
  was a separate product that overwrote ARCHITECTURE/ROADMAP/DESIGN/CLAUDE.
  `aidlc init` now scaffolds a `BRAINDUMP.md` template by default; other
  doc templates remain available via `aidlc init --with-docs`.
- **`aidlc audit`, `aidlc finalize`, `aidlc validate` standalone commands
  removed.** `finalize` and `validate` engines run inside `aidlc run`. The
  `audit` engine remains as a Python module (`aidlc/auditor.py`) but has no
  current CLI surface — see [deprecations.md](deprecations.md).
- **Doc-gap detection is now opt-in** (`doc_gap_detection_enabled: false`
  by default). On mature repos it created spurious planning issues from
  TBD/placeholder markers that were not meaningful gaps.
- **Vague finalization passes removed** (`ssot`, `security`, `abend`).
  Their prompts had drifted into generic checklists with no clear
  definition of done. `docs` and `cleanup` remain. New passes will be
  reintroduced once their prompts and acceptance criteria are nailed down.
  Early-stop fallback (`implementation_finalize_on_early_stop=true`) now
  runs `["cleanup"]` instead of `["ssot", "abend", "cleanup"]`.
- **Session-dir pruning retired.** `session_dir_max_keep` config knob
  removed; it only existed for `aidlc plan` (now gone).

### Added

- `aidlc/project_template/BRAINDUMP.md` — starter template scaffolded by
  `aidlc init` so the customer always has a place to write what they want
  built.



### Documentation
- **ISSUE-001** — Foundation docs pass. New `ARCHITECTURE.md`, `ROADMAP.md`,
  `DESIGN.md`, `CHANGELOG.md` (this file). New
  `docs/MIGRATION-existing-projects.md`. `docs/configuration.md` updated to
  document the actual `providers.<id>.phase_models` schema (the prior flat
  `claude_model_planning` keys never existed in DEFAULTS). `docs/cli-lifecycle.md`
  updated for `aidlc reset` and the new early-stop behavior.

### Fixed
- **ISSUE-002** — `aidlc plan` wizard previously overwrote the full
  ARCHITECTURE/ROADMAP/DESIGN/CLAUDE docs with a chat-summary stub at the
  project root. Root cause: doc generation ran with `allow_edits=True`, so
  Claude wrote the full body via Write *and* returned a summary string that
  the wizard then wrote on top. Switched to `allow_edits=False` so the
  wizard receives the body as text and writes it once.
- **ISSUE-003** — `providers.<id>.default_model` in `.aidlc/config.json` is
  now actually honored. Previously, baked-in DEFAULT
  `providers.<id>.phase_models.<phase>` entries always won, so changing
  `default_model: opus` had no visible effect. New precedence: user
  `phase_models[phase]` → user `default_model` → DEFAULT `phase_models[phase]`
  → DEFAULT `default_model` → adapter default. Router debug log now includes
  the source of the chosen model.
- **ISSUE-004** — Token-exhaustion fallback now walks within a provider's
  model chain before excluding the provider entirely. New per-provider
  `model_fallback_chain` config (default for Claude:
  `["sonnet", "opus", "haiku"]`). Single-provider users no longer have runs
  end at the first quota wall.

### Quality
- **ISSUE-005** — Planner prompt now includes prior-run issues with status,
  under "Prior Run — Already Done (do not redo)". Reduces re-planning of
  shipped work on repeat runs.
- **ISSUE-006** — Planner prompt now includes the first ~2 KB of
  ROADMAP/ARCHITECTURE/DESIGN with a "committed — incremental changes only"
  framing, plus a system-prompt instruction to propose foundation-doc updates
  rather than diverging silently.
- **ISSUE-007** — Implementation prompt tightened: explicit "modify in place;
  rewriting is a last resort" for systems with tests and callers; new
  `existing_callers_checked` field in the JSON output schema.

### Added
- **ISSUE-008** — `aidlc reset` subcommand. Clears `runs/`, `reports/`,
  `issues/`, `session/`, and run artifacts; preserves `config.json` by
  default. Flags: `--all`, `--dry-run`, `--keep-issues`, `--yes`/`-y`.
- **ISSUE-012** — `aidlc run --retry-failed` flag and automatic reopen of
  transient-cause failures (`failed_token_exhausted`, `failed_unknown`) at
  start of each implementation cycle. New `failure_cause` field on issues.

### Changed
- **ISSUE-009** — **Breaking default change**: implementation no longer
  auto-runs `ssot`/`abend`/`cleanup` finalization passes when stopping early
  with work remaining. Set `implementation_finalize_on_early_stop: true` to
  restore the old behavior. Stop reason and resume instructions now logged on
  a single visually-distinct line.
- **ISSUE-010** — Run lifecycle now distinguishes `interrupted` (caught
  signal) and `abandoned` (stale `running`/`interrupted` older than 1 hour)
  from `running`. Resume surfaces abandoned runs.
- **ISSUE-011** — Planning's diminishing-returns threshold is now adaptive to
  issue count: `clamp(min, ceil(num_issues/10), max)` with config keys
  `planning_diminishing_returns_min_threshold` (default 3) and `_max_threshold`
  (default 6). The legacy `diminishing_returns_threshold` config key has been
  removed; remove it from any existing config files.
- **ISSUE-013** — Doc-gap scan results are cached within a run keyed on
  doc-mtime hash; subsequent planning cycles in the same run skip the rescan
  unless docs change. (The accompanying `.aidlc/session/` pruning shipped
  for the original `aidlc plan` wizard, which has since been retired in the
  core-focus audit; the `session_dir_max_keep` knob no longer exists.)
