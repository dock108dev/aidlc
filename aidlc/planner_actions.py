"""Planning action application and dependency normalization."""

from __future__ import annotations

from pathlib import Path

from .models import Issue
from .planner_dependency_graph import sanitize_dependencies
from .schemas import PlanningAction


def apply_action(planner, action: PlanningAction) -> None:
    """Apply a single parsed planning action to state and issue files."""
    if action.action_type == "create_issue":
        issue = Issue(
            id=action.issue_id,
            title=action.title,
            description=action.description or "",
            priority=action.priority or "medium",
            labels=action.labels,
            dependencies=action.dependencies,
            acceptance_criteria=action.acceptance_criteria,
        )
        planner.state.update_issue(issue)
        planner.state.issues_created += 1
        planner.state.total_issues = len(planner.state.issues)

        issues_dir = Path(planner.config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)
        issue_path = issues_dir / f"{action.issue_id}.md"
        issue_path.write_text(planner._render_issue_md(issue))
        planner.logger.info(f"Created issue: {action.issue_id} — {action.title}")

    elif action.action_type == "update_issue":
        existing = planner.state.get_issue(action.issue_id)
        if existing:
            if action.description:
                existing.description = action.description
            if action.priority:
                existing.priority = action.priority
            if action.labels:
                existing.labels = action.labels
            if action.acceptance_criteria:
                existing.acceptance_criteria = action.acceptance_criteria
            if action.dependencies is not None:
                existing.dependencies = action.dependencies
            planner.state.update_issue(existing)

            issues_dir = Path(planner.config["_issues_dir"])
            issue_path = issues_dir / f"{action.issue_id}.md"
            issue_path.write_text(planner._render_issue_md(existing))
            planner.logger.info(f"Updated issue: {action.issue_id}")
        else:
            planner.logger.warning(f"Cannot update unknown issue: {action.issue_id}")


def sanitize_issue_dependencies(planner) -> int:
    """Normalize dependency graph and persist issue markdown files when changed."""
    if not planner.state.issues:
        return 0

    id_to_issue = {d["id"]: d for d in planner.state.issues if d.get("id")}
    touched, total_changes = sanitize_dependencies(planner.state.issues, planner.logger)

    if touched:
        issues_dir = Path(planner.config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)
        for issue_id in sorted(touched):
            issue_data = id_to_issue.get(issue_id)
            if not issue_data:
                continue
            issue = Issue.from_dict(issue_data)
            planner.state.update_issue(issue)
            issue_path = issues_dir / f"{issue_id}.md"
            issue_path.write_text(planner._render_issue_md(issue))
            planner.logger.info(f"Updated issue dependencies: {issue_id}")
    return total_changes
