"""Topological issue ordering with automatic cycle breaking."""

from collections.abc import Callable

from .models import Issue, RunState


def sort_issues_for_implementation(
    state: RunState,
    logger,
    sync_all_issue_markdown: Callable[[], None],
) -> bool:
    """Sort issues by priority and dependency order; break cycles when needed."""
    priority_order = {"high": 0, "medium": 1, "low": 2}

    def detect_cycles(id_to_issue: dict[str, dict]) -> list[list[str]]:
        sorted_ids_local: list[str] = []
        visited: set[str] = set()
        temp_visited: set[str] = set()
        cycles: list[list[str]] = []
        cycle_keys: set[tuple[str, ...]] = set()

        def visit(issue_id: str, path: list[str]) -> None:
            if issue_id in visited:
                return
            if issue_id in temp_visited:
                cycle_start = path.index(issue_id)
                cycle = path[cycle_start:] + [issue_id]
                key = tuple(sorted(cycle[:-1]))
                if key and key not in cycle_keys:
                    cycle_keys.add(key)
                    cycles.append(cycle)
                return
            temp_visited.add(issue_id)
            issue = id_to_issue.get(issue_id, {})
            for dep in issue.get("dependencies", []):
                if dep in id_to_issue:
                    visit(dep, path + [issue_id])
            temp_visited.discard(issue_id)
            visited.add(issue_id)
            sorted_ids_local.append(issue_id)

        priority_sorted_local = sorted(
            state.issues,
            key=lambda d: priority_order.get(d.get("priority", "medium"), 1),
        )
        for data in priority_sorted_local:
            visit(data["id"], [])
        return cycles

    id_to_issue = {d["id"]: d for d in state.issues}
    removed_edges: list[tuple[str, str]] = []
    max_passes = max(1, len(id_to_issue) * 2)

    for _ in range(max_passes):
        cycles = detect_cycles(id_to_issue)
        if not cycles:
            break

        for cycle in cycles:
            cycle_str = " -> ".join(cycle)
            logger.error(f"Circular dependency detected: {cycle_str}. Auto-resolving.")

            core = cycle[:-1]
            if not core:
                continue

            candidate = max(
                core,
                key=lambda issue_id: (
                    priority_order.get(id_to_issue.get(issue_id, {}).get("priority", "medium"), 1),
                    len(id_to_issue.get(issue_id, {}).get("dependencies", [])),
                    issue_id,
                ),
            )
            candidate_idx = core.index(candidate)
            successor = cycle[candidate_idx + 1]

            deps = list(id_to_issue.get(candidate, {}).get("dependencies", []))
            removed = False
            if successor in deps:
                deps.remove(successor)
                removed = True
            else:
                for dep in deps:
                    if dep in core:
                        deps.remove(dep)
                        successor = dep
                        removed = True
                        break

            if removed:
                id_to_issue[candidate]["dependencies"] = deps
                removed_edges.append((candidate, successor))
                logger.warning(f"Removed circular dependency edge: {candidate} -> {successor}")
            else:
                logger.error(
                    f"Could not auto-resolve cycle edge for {candidate}; manual fix required."
                )
                return False
    else:
        logger.error("Dependency cycle resolution exceeded max passes; manual fix required.")
        return False

    if removed_edges:
        touched_issue_ids = {issue_id for issue_id, _ in removed_edges}
        for issue_id in touched_issue_ids:
            issue_data = id_to_issue.get(issue_id)
            if not issue_data:
                continue
            state.update_issue(Issue.from_dict(issue_data))
            logger.info(f"Updated issue dependencies after cycle resolution: {issue_id}")
        sync_all_issue_markdown()

    sorted_ids: list[str] = []
    visited: set[str] = set()
    temp_visited: set[str] = set()

    def topo_visit(issue_id: str) -> None:
        if issue_id in visited:
            return
        if issue_id in temp_visited:
            return
        temp_visited.add(issue_id)
        for dep in id_to_issue.get(issue_id, {}).get("dependencies", []):
            if dep in id_to_issue:
                topo_visit(dep)
        temp_visited.discard(issue_id)
        visited.add(issue_id)
        sorted_ids.append(issue_id)

    priority_sorted = sorted(
        state.issues,
        key=lambda d: priority_order.get(d.get("priority", "medium"), 1),
    )
    for data in priority_sorted:
        topo_visit(data["id"])

    state.issues = [id_to_issue[iid] for iid in sorted_ids if iid in id_to_issue]
    return True
