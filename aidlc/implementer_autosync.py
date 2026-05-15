"""Autosync and issue-markdown synchronization helpers for Implementer."""

from __future__ import annotations

from .implementer_workspace import git_current_branch, git_has_changes
from .models import Issue
from .planner_helpers import render_issue_md


def should_autosync(impl) -> bool:
    """Return true when this implementation cycle should produce an autosync commit."""
    if not impl.autosync_enabled or impl.config.get("dry_run"):
        return False
    return impl.state.implementation_cycles > 0 and (
        impl.state.implementation_cycles % impl.autosync_every_cycles == 0
    )


def autosync_progress(impl) -> bool:
    """Persist issue statuses, commit, push, and prune stale run artifacts."""
    impl.logger.info(
        f"Autosync checkpoint at implementation cycle {impl.state.implementation_cycles}"
    )

    impl._autosync_finalize_before_push_if_enabled()
    if impl.autosync_issue_status_sync:
        impl._sync_all_issue_markdown()

    committed = impl._git_commit_cycle_snapshot(impl.state.implementation_cycles)
    if committed and impl.autosync_push_remote:
        impl._git_push_current_branch()
    if impl.autosync_prune_enabled:
        impl._prune_aidlc_data()
    return committed


def sync_issue_markdown(impl, issue: Issue) -> None:
    """Keep .aidlc issue markdown aligned with in-memory status and notes."""
    if not impl.autosync_issue_status_sync:
        return
    try:
        impl.issues_dir.mkdir(parents=True, exist_ok=True)
        issue_path = impl.issues_dir / f"{issue.id}.md"
        issue_path.write_text(render_issue_md(issue))
    except OSError as e:
        impl.logger.warning(f"Failed to sync issue file for {issue.id}: {e}")


def sync_all_issue_markdown(impl) -> None:
    """Refresh every issue markdown file from state."""
    for d in impl.state.issues:
        try:
            sync_issue_markdown(impl, Issue.from_dict(d))
        except Exception as e:
            issue_id = d.get("id", "unknown") if isinstance(d, dict) else "unknown"
            impl.logger.warning(f"Issue sync skipped for {issue_id}: {e}")


def git_current_branch_name(impl) -> str | None:
    return git_current_branch(impl.project_root, impl.state, impl.logger)


def git_tree_has_changes(impl) -> bool:
    return git_has_changes(impl.project_root, impl.state, impl.logger)
