"""Best-effort reconciliation when resuming implementation — detect done work without replanning.

**Off by default** (``resume_reconcile_enabled = False``). The heuristic
flips ``pending``/``in_progress`` issues to ``implemented`` based on
whether the issue id appears in committed source files. False positives
here are **very expensive** — validation later runs against a stale
"done" status, the implementer never re-attempts, the user thinks the
work shipped when it didn't. False negatives are cheap — aidlc just
re-runs an issue that was already done.

Concrete failure modes the heuristic can't reliably distinguish:

  - Foundation docs (BRAINDUMP, ROADMAP, ARCHITECTURE) often *mention*
    planned issue IDs as part of the plan. That is evidence of
    *planning*, not *completion*.
  - Earlier Claude work may leave comments referencing future issue
    IDs (e.g. "TODO: addressed by ISSUE-013") even when the prompt
    forbids it.
  - The issue file at ``.aidlc/issues/<id>.md`` is excluded from the
    grep, but a copy/quote in any other file is enough to trip the
    heuristic.

The legitimate use case (user manually completes some issues between
runs and references their IDs in commits) is uncommon enough that
we don't make the user pay for it by default. Set
``resume_reconcile_enabled: true`` if you want the convenience.

Concrete guard rails when enabled (all must hold for a flip):

  1. Current status is ``pending`` or ``in_progress``. ``failed`` /
     ``implemented`` / ``verified`` / ``skipped`` are left alone.
  2. ``attempt_count == 0``. An issue with attempts already recorded was
     actively being worked on this run; trust the recorded status.
  3. The issue is **not** the run's currently-active ``current_issue_id``
     (that's the issue the implementer was mid-flight on when killed —
     trust the in-progress status, never flip).
  4. The issue id appears in **at least one non-test file** in the git
     tree. Tests and fixtures often carry the issue id in filenames
     (e.g. ``tests/test_retro_games_scene_issue_006.gd``) before the
     implementation has finished — finding the id only in test paths
     is not evidence of completion.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import Issue, IssueStatus, RunState

_RECONCILE_NOTE = (
    "[aidlc resume] Auto-implemented (issue id found in source files outside .aidlc/)."
)

# Path components treated as "test scaffolding" — references here do not
# count as evidence of a completed implementation. Matched against
# normalized directory segments (so ``__tests__/foo.test.ts`` matches via
# the ``__tests__`` segment regardless of leading slash).
_TEST_DIR_SEGMENTS = frozenset(
    {"test", "tests", "spec", "specs", "__tests__", "__test__", "gut", "testing"}
)
_TEST_FILENAME_PREFIXES = ("test_", "spec_")
# Common test-suffix patterns (Go: ``foo_test.go``; JS/TS: ``foo.test.ts``,
# ``foo.spec.ts``).
_TEST_FILENAME_INFIXES = (".test.", ".spec.", "_test.", "_spec.")


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


def _looks_like_test_path(path: str) -> bool:
    """True when ``path`` is a test file or lives under a test directory."""
    p = (path or "").lower().replace("\\", "/").strip("/")
    if not p:
        return True  # treat empty as "ignore" — defensively conservative
    parts = p.split("/")
    name = parts[-1]
    if any(name.startswith(prefix) for prefix in _TEST_FILENAME_PREFIXES):
        return True
    if any(infix in name for infix in _TEST_FILENAME_INFIXES):
        return True
    # Any directory segment matching a known test path component.
    for seg in parts[:-1]:
        if seg in _TEST_DIR_SEGMENTS:
            return True
    return False


def _issue_id_in_non_test_source(project_root: Path, issue_id: str) -> bool:
    """True when ``git grep`` finds the issue id in at least one non-test file."""
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
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    paths = [p for p in proc.stdout.splitlines() if p.strip()]
    return any(not _looks_like_test_path(p) for p in paths)


def reconcile_issues_on_resume(
    state: RunState,
    project_root: Path,
    logger,
    config: dict | None = None,
) -> int:
    """Mark likely-completed issues so implementation can continue without new planning.

    See module docstring for the (deliberately tight) guard rails. Returns
    the number of issues updated.
    """
    cfg = config or {}
    if not cfg.get("resume_reconcile_enabled", False):
        return 0

    active_id = state.current_issue_id

    updated = 0
    updated_ids: list[str] = []
    skipped_with_attempts = 0
    skipped_test_only = 0
    skipped_active = 0
    for d in list(state.issues):
        if not isinstance(d, dict):
            continue
        st = d.get("status")
        if st not in ("pending", "in_progress"):
            continue
        # Don't override issues that were actively being worked on in
        # this run. attempt_count > 0 means the implementer touched it;
        # trust the status it recorded (failure, partial, etc.).
        attempts = int(d.get("attempt_count") or 0)
        if attempts > 0:
            skipped_with_attempts += 1
            continue
        issue_id = d.get("id")
        if not issue_id or not isinstance(issue_id, str):
            continue
        # Defense-in-depth: the issue the implementer was mid-flight on
        # when the run was killed has ``current_issue_id == its id`` in
        # state.json. Never flip that one even if attempt_count somehow
        # reads as 0.
        if active_id and issue_id == active_id:
            skipped_active += 1
            continue
        if not _issue_id_in_non_test_source(project_root, issue_id):
            # The id may still appear under tests/ — just not enough
            # evidence to claim the implementation finished.
            skipped_test_only += 1
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
            "Resume reconcile: marked %s issue(s) implemented "
            "(id found in non-test source outside .aidlc/) — e.g. %s%s",
            updated,
            sample,
            more,
        )
    if skipped_with_attempts:
        logger.info(
            "Resume reconcile: left %s issue(s) alone because they have prior attempts "
            "(trust recorded status over heuristic).",
            skipped_with_attempts,
        )
    if skipped_active:
        logger.info(
            "Resume reconcile: left %s issue(s) alone because they were the active "
            "in-flight issue when the prior run stopped.",
            skipped_active,
        )
    if skipped_test_only:
        logger.debug(
            "Resume reconcile: %s issue(s) had id references only in test files — not enough "
            "evidence to flip; left as-is.",
            skipped_test_only,
        )

    return updated
