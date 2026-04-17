# Large Python modules (>500 lines)

We aim to keep most modules under ~500 lines for readability. Some modules intentionally remain larger: they are central orchestrators or single cohesive engines. This list is a **review queue** for future refactors (split helpers, not micro-fragments).

| File | Approx. LOC | Notes |
|------|----------------|-------|
| `aidlc/planner.py` | ~700 | Planning loop, cycles, guard rails; primary split would be cycle handlers / action dispatch. |
| `aidlc/implementer.py` | ~650 | Implementation loop and verification; split candidates: autosync/git vs issue execution. |
| `aidlc/planner_helpers.py` | ~650 | Shared planning prompts and issue/doc rendering. |
| `aidlc/audit/output_engine.py` | ~640 | Single audit output pipeline. |
| `aidlc/models.py` | ~496 | Run state, serialization, telemetry; `Issue`/`IssueStatus` live in `issue_model.py`. |
| `aidlc/routing/engine.py` | ~514 | Provider router and retry/cooldown loop; tightly coupled to routing types. |

**Test files** over 500 lines (e.g. `tests/test_implementer_extended.py`, `tests/test_planner.py`) are accepted as integration-heavy suites; prefer extracting **helpers/fixtures** over splitting arbitrary test class chunks.

Regenerate approximate counts with:

```bash
wc -l aidlc/**/*.py tests/**/*.py | sort -n | awk '$1>500'
```
