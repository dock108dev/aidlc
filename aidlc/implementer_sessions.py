"""Implementation-session continuation file helpers."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .models import Issue


def impl_continuation_path(run_dir: Path, issue: Issue) -> Path:
    """Return the provider continuation map path for an issue attempt."""
    return (
        run_dir
        / "claude_sessions"
        / f"impl_{issue.id}_a{issue.attempt_count:02d}.continuation.json"
    )


def legacy_impl_uuid_path(run_dir: Path, issue: Issue) -> Path:
    """Return the pre-map Claude UUID path used by older interrupted runs."""
    return run_dir / "claude_sessions" / f"impl_{issue.id}_a{issue.attempt_count:02d}.uuid"


def save_impl_continuation(run_dir: Path, issue: Issue, data: dict[str, str | None]) -> None:
    """Persist per-provider continuation ids for this issue attempt."""
    path = impl_continuation_path(run_dir, issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def load_or_create_impl_continuation(
    run_dir: Path,
    issue: Issue,
    config: dict,
    resuming: bool,
) -> dict[str, str | None]:
    """Load or create provider session hints for this implementation attempt."""
    del resuming  # Kept for the caller-facing method signature.
    if not config.get("claude_implementation_cli_threading", True):
        return {}

    sess_dir = run_dir / "claude_sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    path = impl_continuation_path(run_dir, issue)
    legacy = legacy_impl_uuid_path(run_dir, issue)

    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        if isinstance(raw, dict):
            return {
                "claude": raw.get("claude") if raw.get("claude") else None,
                "openai": raw.get("openai") if raw.get("openai") else None,
                "copilot": raw.get("copilot") if raw.get("copilot") else None,
            }

    if legacy.exists():
        cid = legacy.read_text(encoding="utf-8").strip() or None
        out: dict[str, str | None] = {
            "claude": cid,
            "openai": None,
            "copilot": str(uuid.uuid4()),
        }
        save_impl_continuation(run_dir, issue, out)
        return out

    out = {
        "claude": str(uuid.uuid4()),
        "openai": None,
        "copilot": str(uuid.uuid4()),
    }
    save_impl_continuation(run_dir, issue, out)
    return out
