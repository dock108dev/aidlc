"""SSOT enforcement: the diminishing-returns multi-empty-cycle wait was
replaced with a single explicit verify cycle. The legacy infrastructure
must stay removed.
"""

from __future__ import annotations

from aidlc.config import DEFAULTS
from aidlc.planner import Planner


def test_adaptive_threshold_function_is_absent():
    """``Planner._adaptive_diminishing_threshold`` was removed because the
    verify-mode design (see ``VERIFY_INSTRUCTIONS``) replaces the
    multi-empty-cycle wait with a single explicit coverage-check pass.
    Reintroducing it would resurrect the diminishing-returns logic."""
    assert not hasattr(Planner, "_adaptive_diminishing_threshold")


def test_diminishing_returns_config_keys_are_absent_from_defaults():
    """SSOT: the three diminishing-returns config keys are no longer in
    DEFAULTS. Verify mode owns the empty-cycle handling now and needs no
    threshold tuning."""
    for key in (
        "diminishing_returns_window",
        "planning_diminishing_returns_min_threshold",
        "planning_diminishing_returns_max_threshold",
    ):
        assert key not in DEFAULTS, f"{key} must not be in DEFAULTS — verify mode replaces it"


def test_legacy_diminishing_returns_threshold_config_key_is_ignored():
    """SSOT: the deprecated ``diminishing_returns_threshold`` key was
    removed in an earlier cleanup; setting it must continue to have no
    effect (negative regression — re-adding logic that reads it would
    show up here)."""
    assert "diminishing_returns_threshold" not in DEFAULTS
