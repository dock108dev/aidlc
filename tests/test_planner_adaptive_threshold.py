"""ISSUE-011: planning's diminishing-returns threshold scales with issue count.

Effective threshold = ``clamp(min, ceil(num_issues_so_far / 10), max)``.
Default min=3, max=6, so:

  ≤ 30 issues → 3
  31-40 → 4
  41-50 → 5
  ≥ 51 → 6 (or whatever the current max is)
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from aidlc.planner import Planner


def _make_planner(tmp_path, num_issues: int, config_overrides: dict | None = None):
    """Build a minimal Planner instance to exercise _adaptive_diminishing_threshold."""
    config = {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(tmp_path / ".aidlc"),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_runs_dir": str(tmp_path / ".aidlc" / "runs"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
    }
    if config_overrides:
        config.update(config_overrides)
    (tmp_path / ".aidlc" / "issues").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".aidlc" / "runs" / "test").mkdir(parents=True, exist_ok=True)

    from aidlc.models import RunState

    state = RunState(run_id="t", config_name="default")
    state.issues = [{"id": f"ISSUE-{i:03d}", "title": "x"} for i in range(num_issues)]

    return Planner(
        state=state,
        run_dir=tmp_path / ".aidlc" / "runs" / "test",
        config=config,
        cli=MagicMock(),
        project_context="ctx",
        logger=logging.getLogger("test.planner.adaptive"),
    )


@pytest.mark.parametrize(
    "num_issues,expected",
    [
        (0, 3),  # floor
        (10, 3),  # ceil(10/10)=1, clamped to floor 3
        (25, 3),  # ceil(25/10)=3
        (30, 3),  # ceil(30/10)=3
        (31, 4),  # ceil(31/10)=4
        (40, 4),  # ceil(40/10)=4
        (50, 5),  # ceil(50/10)=5
        (51, 6),  # ceil(51/10)=6
        (60, 6),  # ceil(60/10)=6
        (100, 6),  # ceil(100/10)=10, clamped to ceiling 6
    ],
)
def test_adaptive_threshold_scales_with_issue_count(tmp_path, num_issues, expected):
    p = _make_planner(tmp_path, num_issues)
    assert p._adaptive_diminishing_threshold() == expected


def test_adaptive_threshold_signature_is_zero_arg(tmp_path):
    """SSOT: ``_adaptive_diminishing_threshold`` no longer accepts a
    ``legacy_threshold`` argument. The deprecated ``diminishing_returns_threshold``
    config key was fully removed; only ``planning_diminishing_returns_min_threshold``
    / ``_max_threshold`` shape the threshold."""
    p = _make_planner(tmp_path, num_issues=10)
    with pytest.raises(TypeError):
        p._adaptive_diminishing_threshold(legacy_threshold=5)


def test_legacy_diminishing_returns_threshold_config_key_is_ignored(tmp_path):
    """SSOT: setting the legacy ``diminishing_returns_threshold`` has no effect
    on the computed threshold (no compat shim, no deprecation warning, no read)."""
    p_with_legacy = _make_planner(
        tmp_path,
        num_issues=10,
        config_overrides={"diminishing_returns_threshold": 99},
    )
    p_without = _make_planner(tmp_path, num_issues=10)
    assert (
        p_with_legacy._adaptive_diminishing_threshold()
        == p_without._adaptive_diminishing_threshold()
    )


def test_adaptive_threshold_min_max_overrides(tmp_path):
    p = _make_planner(
        tmp_path,
        num_issues=100,
        config_overrides={
            "planning_diminishing_returns_min_threshold": 4,
            "planning_diminishing_returns_max_threshold": 8,
        },
    )
    # ceil(100/10)=10; clamp(4, 10, 8) = 8
    assert p._adaptive_diminishing_threshold() == 8


def test_adaptive_threshold_min_above_default_max_promotes_max(tmp_path):
    """If a user sets min > max, the function honors min."""
    p = _make_planner(
        tmp_path,
        num_issues=10,
        config_overrides={
            "planning_diminishing_returns_min_threshold": 8,
            "planning_diminishing_returns_max_threshold": 5,  # bogus inversion
        },
    )
    # max promoted to floor; clamp(8, 1, 8) = 8
    assert p._adaptive_diminishing_threshold() == 8
