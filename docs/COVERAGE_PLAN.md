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
| Missed | ~3,029 |
| **Line coverage** | **~61%** |
| `pyproject.toml` `fail_under` | 58 until you complete the next milestone |

Already covered in this tree (re-check with `term-missing`): `context_prep.py`, `plan_templates.py` — treat as done; do not duplicate work there.

**Gap to long-term ~90%:** still on the order of **~2,000+** newly covered statements (honest measurement, minimal `omit`). The **next** milestone below breaks off only the next **~780** hits.

---

## Next milestone only: **~61% → ~71%** (+10 points)

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

**When ~71% is real:** set `fail_under` to **69** or **70** (slack), update the table in this doc, and open the next section “**~71% → ~81%**” with a fresh shortlist (re-run `term-missing`—the worst offenders will have shifted).

---

**Gap to ~90% (whole journey):** after this milestone you still need on the order of **~1,500+** more covered statements across later cycles—same honesty as before, just chunked.

---

## Biggest holes (highest impact first)

These modules drive most of the gap. Tackle in order of **missed statements × ease of testing**.

### A. Zero (or ~0%) coverage — whole modules untested

| Module | ~Stmts missed | Notes |
|--------|--------------:|--------|
| `aidlc/plan_session.py` | ~244 | Interactive / session flow; needs orchestration tests or refactor for testability |
| `aidlc/improve.py` | ~189 | CLI-style flow; similar |
| `aidlc/plan_wizard.py` | ~104 | Wizard prompts; use `stdin`/`pytest` monkeypatch (**still open** — top item for 61→71%) |
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
| `aidlc/routing/strategy_resolution.py` | ~32% | Add matrix tests per `RoutingStrategy` + edge cases |
| `aidlc/routing/result_signals.py` | ~53% | Parser helpers: table-driven tests from sample stderr/stdout |
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
