"""Helpers for run time attribution (Claude CLI vs local console)."""

import time

from .models import RunState


def add_console_time(state: RunState, started_at: float) -> None:
    """Add wall time since ``started_at`` to ``console_seconds`` (local subprocess work)."""
    state.console_seconds += max(0.0, time.time() - started_at)
