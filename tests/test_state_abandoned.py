"""ISSUE-010: stale RUNNING/INTERRUPTED runs surface as ABANDONED on resume.

Covers:
- ``RunStatus.INTERRUPTED`` and ``RunStatus.ABANDONED`` enum members exist.
- ``is_run_abandoned`` returns True only for stale RUNNING/INTERRUPTED runs.
- ``mark_abandoned_if_stale`` flips status and persists state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aidlc.models import RunPhase, RunState, RunStatus
from aidlc.state_manager import (
    is_run_abandoned,
    load_state,
    mark_abandoned_if_stale,
    save_state,
)


def _state(status: RunStatus, last_updated: str | None = None) -> RunState:
    s = RunState(run_id="t", config_name="default")
    s.status = status
    s.phase = RunPhase.IMPLEMENTING
    s.last_updated = last_updated
    return s


def test_run_status_has_interrupted_and_abandoned_members():
    assert RunStatus.INTERRUPTED.value == "interrupted"
    assert RunStatus.ABANDONED.value == "abandoned"


def test_is_run_abandoned_fresh_running_is_not():
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert is_run_abandoned(_state(RunStatus.RUNNING, fresh)) is False


def test_is_run_abandoned_stale_running_is():
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert is_run_abandoned(_state(RunStatus.RUNNING, stale)) is True


def test_is_run_abandoned_stale_interrupted_is():
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert is_run_abandoned(_state(RunStatus.INTERRUPTED, stale)) is True


def test_is_run_abandoned_complete_is_not():
    """A completed run is not abandoned regardless of age."""
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert is_run_abandoned(_state(RunStatus.COMPLETE, stale)) is False


def test_is_run_abandoned_missing_last_updated_is():
    """Pathological case: very old states without last_updated count as abandoned."""
    assert is_run_abandoned(_state(RunStatus.RUNNING, None)) is True


def test_is_run_abandoned_threshold_override():
    """Custom threshold lets callers tune sensitivity for tests/CI."""
    recent = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    s = _state(RunStatus.RUNNING, recent)
    assert is_run_abandoned(s, threshold_seconds=600) is True  # 10 min threshold
    assert is_run_abandoned(s, threshold_seconds=3600) is False


def test_mark_abandoned_persists(tmp_path):
    """Calling mark_abandoned_if_stale flips status AND saves to disk.

    Note: ``save_state`` rewrites ``last_updated`` to now, so for this test we
    set the in-memory ``last_updated`` to a stale value AFTER the initial save
    — the abandonment check is in-memory, then the post-flip save records the
    new (fresh) timestamp on the ABANDONED record.
    """
    run_dir = tmp_path / "runs" / "aidlc_x"
    run_dir.mkdir(parents=True)
    state = _state(RunStatus.RUNNING)
    save_state(state, run_dir)
    state.last_updated = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    flipped = mark_abandoned_if_stale(state, run_dir)
    assert flipped is True
    assert state.status == RunStatus.ABANDONED

    reloaded = load_state(run_dir)
    assert reloaded.status == RunStatus.ABANDONED
    assert "abandoned" in (reloaded.stop_reason or "").lower()


def test_mark_abandoned_no_op_when_fresh(tmp_path):
    run_dir = tmp_path / "runs" / "aidlc_x"
    run_dir.mkdir(parents=True)
    state = _state(RunStatus.RUNNING)
    save_state(state, run_dir)  # fresh last_updated written by save_state

    flipped = mark_abandoned_if_stale(state, run_dir)
    assert flipped is False
    assert state.status == RunStatus.RUNNING