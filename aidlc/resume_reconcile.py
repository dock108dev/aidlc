"""Best-effort reconciliation when resuming implementation — detect done work without replanning."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import Issue, IssueStatus, RunState

_RECONCILE_NOTE = "[aidlc resume] Auto-implemented (issue id found in repo outside .aidlc/)."


def _git_repo_root(project_root: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return None
        root = proc.stdout.strip()
        return Path(root) if root else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _issue_id_referenced_in_tree(project_root: Path, issue_id: str) -> bool:
    """True when `git grep` finds a fixed-string issue id outside ``.aidlc/``."""
    root = _git_repo_root(project_root)
    if root is None:
        return False
    try:
        proc = subprocess.run(
            [
                "git",
                "grep",
                "-l",
                "-F",
                issue_id,
                "--",
                ":!.aidlc",
                ":!.git",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def reconcile_issues_on_resume(
    state: RunState,
    project_root: Path,
    logger,
    config: dict | None = None,
) -> int:
    """Mark likely-completed issues so implementation can continue without new planning.

    Conservative heuristic: if the issue id appears in the tracked tree outside ``.aidlc/``,
    flip **pending** / **in_progress** to **implemented** and append a resume note.

    Returns the number of issues updated.
    """
    cfg = config or {}
    if not cfg.get("resume_reconcile_enabled", True):
        return 0

    updated = 0
    updated_ids: list[str] = []
    for d in list(state.issues):
        if not isinstance(d, dict):
            continue
        st = d.get("status")
        if st not in ("pending", "in_progress"):
            continue
        issue_id = d.get("id")
        if not issue_id or not isinstance(issue_id, str):
            continue
        if not _issue_id_referenced_in_tree(project_root, issue_id):
            continue

        issue = Issue.from_dict(d)
        if issue.status not in (IssueStatus.PENDING, IssueStatus.IN_PROGRESS):
            continue
        issue.status = IssueStatus.IMPLEMENTED
        note = _RECONCILE_NOTE
        prev = (issue.implementation_notes or "").strip()
        issue.implementation_notes = f"{prev}\n\n{note}".strip() if prev else note
        state.update_issue(issue)
        updated += 1
        updated_ids.append(issue_id)

    if updated:
        sample = ", ".join(updated_ids[:5])
        more = f" (+{updated - 5} more)" if updated > 5 else ""
        logger.info(
            "Resume reconcile: marked %s issue(s) implemented (id found in git tree outside .aidlc/) "
            "— e.g. %s%s",
            updated,
            sample,
            more,
        )

    return updated
