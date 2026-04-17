# Test coverage plan (long-term target: ~90% on `aidlc/`)

## Iteration policy (+10 points at a time)

Raise coverage in **~10 percentage-point** steps (e.g. 61% → 71% → 81% → …). After each milestone:

1. Run `pytest --cov=aidlc --cov-report=term-missing` and confirm the new total.
2. Bump **`[tool.coverage.report] fail_under`** in `pyproject.toml` to **just below** the new steady total (leave ~1 point slack so tiny refactors do not fail CI).
3. Ship the milestone in one or a few PRs; avoid giant “coverage only” dumps that are hard to review.

Long-term **~90%** stays the north star; each cycle only commits to the **next** +10 points.

---

## Where we are (update when you finish a milestone)

Run this from the repo root (requires `pip install -e ".[dev]"`):

```bash
# Summary + missing line numbers in the terminal
python -m pytest --cov=aidlc --cov-report=term-missing -q

# HTML report (open htmlcov/index.html in a browser)
python -m pytest --cov=aidlc --cov-report=html -q
```

**Latest snapshot (re-run after changes):**

| Metric | Value |
|--------|------:|
| Statements (`aidlc/`) | 7,831 |
| Missed | ~1,507 |
| **Line coverage** | **~80.8%** |
| `pyproject.toml` `fail_under` | 79 (~1 pp slack below steady total) |

Recent high-yield additions include `planner_helpers` (research + planning index), `reporting`, `config_detect`, `__main__` dispatch and `cmd_run` branches, `runner` orchestration (audit, doc gaps, interrupts, errors), `implementer` / `implementer_helpers`, `claude_cli._extract_cli_metadata`, `test_profiles`, and supporting tests across `plan_session`, `improve`, and `doc_gap_detector`.

**Gap to long-term ~90%:** on the order of **~700+** newly covered statements (honest measurement, minimal `omit`).

---

## Completed milestone: **~61% → ~70.7%** (target was ~71%; within ~0.3 pp)

**Math (line coverage on full `aidlc/` tree):**  
At ~7,831 statements, **+10 percentage points** means roughly **~780 fewer missed lines** (newly executed statements), not “ten more tests.”

**Priority order for this milestone** (high yield, mostly unit-testable):

| Priority | Module | Why this milestone |
|----------|---------|--------------------|
| 1 | `aidlc/plan_wizard.py` | Large miss count; `input`/`EOF`, `_auto_detect`, `_strip_starter_comments`, `run_wizard` branches |
| 2 | `aidlc/cli/usage_cmd.py` | Temp `.aidlc/runs/` + `load_state`; few external deps |
| 3 | `aidlc/cli/display.py` | `capsys` / trivial calls on color helpers and banners |
| 4 | `aidlc/routing/result_signals.py` | Table tests from sample stdout/stderr strings |
| 5 | `aidlc/routing/strategy_resolution.py` | Matrix over `RoutingStrategy` + excluded providers/models |
| 6 | `aidlc/validator.py` | Pass/fail fixtures; big miss block, mostly pure logic |
| 7 | `aidlc/implementer_workspace.py` | Temp git repo: `get_changed_files`, `git_has_changes`, prune |
| 8 | `aidlc/accounts/credentials.py` + `manager.py` | Tmp paths + mocks for file CRUD |

**Defer to the following +10% milestone** if timeboxed: `plan_session.py`, `improve.py`, `cli_commands.py` deep paths (heavy I/O / subprocess), `full_engine.py` / `runtime_engine.py` (large integration surface).

**Gate (historical):** the **61→71** milestone used `fail_under` **69** in `pyproject.toml`.

---

## Completed milestone: **~71% → ~81%** (+10 points; steady **~80.8%**)

This cycle focused on `planner_helpers`, `plan_session` / `improve` (from earlier work in this tree), `runner`, `reporting`, `config` merge/load, `__main__`, `implementer` edge paths, `claude_cli` metadata parsing, `config_detect`, `test_profiles`, `doc_gap_detector`, and small gaps in `scanner` / `research_output` / `context_utils`.

**Gate:** `fail_under` is set to **79** in `pyproject.toml` (~1 pp slack vs ~80.8% measured).

---

## Next milestone only: **~81% → ~91%** (+10 points)

Re-run `python -m pytest --cov=aidlc --cov-report=term-missing -q` and refresh this shortlist; likely high-yield targets include:

| Priority | Module | Notes |
|----------|--------|--------|
| 1 | `aidlc/cli_commands.py` / `cli/config_cmd.py` / `cli/accounts.py` | argv + subprocess / large CLI surface |
| 2 | `aidlc/audit/full_engine.py`, `runtime_engine.py` | fixture projects + engine table tests |
| 3 | `aidlc/__main__.py` | remaining `cmd_run` branches |
| 4 | `aidlc/implementer.py` | autosync / workspace paths not yet saturated |
| 5 | `aidlc/providers/*.py` | adapter matrix with fakes |

**When ~91% is real:** set `fail_under` to **~89**, update this table, and add “**~91% → ~100%**” (or declare ~90% the practical ceiling if diminishing returns).

---

**Gap to ~90% (whole journey):** after the next milestone you still need on the order of **~1,000+** more covered statements across later cycles.

---

## Biggest holes (highest impact first)

These modules drive most of the gap. Tackle in order of **missed statements × ease of testing**.

### A. Zero (or ~0%) coverage — whole modules untested

| Module | ~Stmts missed | Notes |
|--------|--------------:|--------|
| `aidlc/plan_session.py` | ~244 | Interactive / session flow; needs orchestration tests or refactor for testability |
| `aidlc/improve.py` | ~189 | CLI-style flow; similar |
| `aidlc/plan_wizard.py` | — | Largely covered for 61→71; re-check `term-missing` for stragglers |
| ~~`aidlc/context_prep.py`~~ | — | Covered |
| ~~`aidlc/plan_templates.py`~~ | — | Covered |

**Plan:** For each, either (1) add **thin integration tests** that call the public entry with fakes/mocks, or (2) **extract pure logic** into testable helpers and keep I/O at the edges.

### B. CLI packages — lots of `print` / `input` / subprocess

| Module | ~Cover | Notes |
|--------|--------:|--------|
| `aidlc/cli/config_cmd.py` | ~7% | Wizard + `input()`; test with `StringIO` / `monkeypatch` |
| `aidlc/cli/provider.py` | ~10% | Auth paths; mock `subprocess.run`, `ProviderRouter` |
| `aidlc/cli/usage_cmd.py` | ~9% | Disk + `load_state`; temp dirs + fixture runs |
| `aidlc/cli/accounts.py` | ~12% | Mostly print; mock `AccountManager` |
| `aidlc/cli/display.py` | ~26% | Capture `capsys` or assert no throw |
| `aidlc/cli_commands.py` | ~31% | Many command handlers; subprocess to `aidlc` or import + `capsys` |

**Plan:** Prefer **import-under-test + monkeypatch** over end-to-end CLI for speed; add a few **smoke** CLI tests if you need regression on argv parsing.

### C. Accounts + audit engines — medium coverage, large misses

| Module | ~Cover | Notes |
|--------|--------:|--------|
| `aidlc/accounts/credentials.py` | ~22% | File / keychain paths; temp dirs + mocks |
| `aidlc/accounts/manager.py` | ~23% | CRUD paths; in-memory or tmp store |
| `aidlc/audit/full_engine.py` | ~23% | Drive with small fixture projects |
| `aidlc/audit/runtime_engine.py` | ~24% | Same |
| `aidlc/implementer_workspace.py` | ~23% | Git + prune; temp git repo fixtures |

### D. Routing — strategic logic under-tested

| Module | ~Cover | Notes |
|--------|--------:|--------|
| `aidlc/routing/strategy_resolution.py` | — | High coverage; finish stragglers via `term-missing` |
| `aidlc/routing/result_signals.py` | — | Same |
| `aidlc/routing/engine.py` | ~72% | Cooldowns, `execute_prompt` branches, fallbacks |

### E. Already strong — finish the last miles

Examples: `models.py` ~97%, `auditor.py` ~96%, `schemas.py` ~94%. Small targeted tests close `Missing` line ranges cheaply.

---

## Backlog reference (themes; execution order = current +10% milestone above)

Use **`term-missing`** after each milestone to refresh priorities—the worst files change as you cover code.

- **CLI / accounts / audit engines:** `cli_commands.py`, `cli/*`, `accounts/*`, `audit/*_engine.py`, `implementer_workspace.py` (see milestone table for what to pull into *this* +10% vs next).
- **Routing / providers:** `strategy_resolution.py`, `result_signals.py`, `engine.py`, adapter modules.
- **Heavy orchestration:** `plan_session.py`, `improve.py`—usually easier in a *later* +10% once more surface is mockable.
- **Guardrails:** keep **`omit`** minimal; prefer real tests over shrinking the denominator.

---

## Commands cheat sheet

```bash
# Full suite + terminal missing lines
python -m pytest --cov=aidlc --cov-report=term-missing

# Only one package while iterating
python -m pytest tests/test_routing_engine.py --cov=aidlc.routing --cov-report=term-missing

# JSON for tooling / dashboards
python -m pytest --cov=aidlc --cov-report=json -q
```

---

## Realistic note on “90%”

Reaching **90% on the full `aidlc` tree** with **honest** measurement (minimal `omit`) is achievable, but it is **several hundred to a few thousand** new or expanded tests’ worth of work unless you **refactor for testability** (smaller functions, fewer giant CLI blocks, dependency injection for subprocess/git/network).

Use **phased `fail_under`** in CI so the main branch stays green while coverage climbs.
