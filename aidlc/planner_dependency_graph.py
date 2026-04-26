"""Dependency-graph normalization for the planner.

Extracted from `planner.py` so the planner stays focused on the cycle
loop. This module contains the pure logic that:

  * drops non-string / empty / self / unknown / duplicate dependency
    edges,
  * detects cycles, and
  * breaks each detected cycle by removing one edge — heuristic prefers
    edges whose source is the lower-priority / heavier-dependency node so
    the leaf-most/most-tangled issue absorbs the loss.

Side-effects (writing markdown, calling ``state.update_issue``) stay in
``planner.py``; this module only mutates the in-memory issue list and
reports back which IDs were touched.
"""

from __future__ import annotations

import logging
from typing import Iterable

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _detect_cycles(id_to_issue: dict[str, dict]) -> list[list[str]]:
    """Find unique cycles in the dependency graph.

    Returns a list of cycles, each as a list of IDs ending with the same
    ID it started with (so consumers can read off the closing edge).
    Cycle keys are deduplicated by sorted-id tuple to avoid emitting the
    same cycle multiple times under different rotations.
    """
    cycles: list[list[str]] = []
    cycle_keys: set[tuple[str, ...]] = set()
    visited: set[str] = set()
    temp: set[str] = set()

    def visit(issue_id: str, path: list[str]) -> None:
        if issue_id in visited:
            return
        if issue_id in temp:
            start = path.index(issue_id)
            cycle = path[start:] + [issue_id]
            key = tuple(sorted(cycle[:-1]))
            if key and key not in cycle_keys:
                cycle_keys.add(key)
                cycles.append(cycle)
            return
        temp.add(issue_id)
        for dep in id_to_issue.get(issue_id, {}).get("dependencies", []):
            if dep in id_to_issue:
                visit(dep, path + [issue_id])
        temp.discard(issue_id)
        visited.add(issue_id)

    for issue_id in sorted(id_to_issue.keys()):
        visit(issue_id, [])
    return cycles


def _scrub_edges(
    id_to_issue: dict[str, dict],
    issue_ids: set[str],
    logger: logging.Logger,
    touched: set[str],
) -> int:
    """Drop bad edges (non-string / empty / self / unknown / duplicate)."""
    total_changes = 0
    for issue_id, issue_data in id_to_issue.items():
        deps = issue_data.get("dependencies") or []
        if not isinstance(deps, list):
            deps = []
        cleaned: list[str] = []
        seen: set[str] = set()
        for dep in deps:
            if not isinstance(dep, str):
                total_changes += 1
                touched.add(issue_id)
                logger.warning(f"Dropped non-string dependency on {issue_id}: {dep!r}")
                continue
            dep_norm = dep.strip().upper()
            if not dep_norm:
                total_changes += 1
                touched.add(issue_id)
                logger.warning(f"Dropped empty dependency on {issue_id}")
                continue
            if dep_norm == issue_id:
                total_changes += 1
                touched.add(issue_id)
                logger.warning(f"Removed self-dependency: {issue_id} -> {dep_norm}")
                continue
            if dep_norm not in issue_ids:
                total_changes += 1
                touched.add(issue_id)
                logger.warning(
                    f"Removed unknown dependency: {issue_id} -> {dep_norm} (target missing)"
                )
                continue
            if dep_norm in seen:
                total_changes += 1
                touched.add(issue_id)
                logger.warning(f"Removed duplicate dependency: {issue_id} -> {dep_norm}")
                continue
            seen.add(dep_norm)
            cleaned.append(dep_norm)
        issue_data["dependencies"] = cleaned
    return total_changes


def _break_one_cycle(
    cycle: list[str],
    id_to_issue: dict[str, dict],
    logger: logging.Logger,
    touched: set[str],
) -> bool:
    """Break a single cycle by removing one edge. Return True if removed."""
    core = cycle[:-1]
    if not core:
        return False

    cycle_str = " -> ".join(cycle)
    logger.warning(f"Circular dependency detected during planning: {cycle_str}")

    # Pick the candidate whose edge is safest to drop: lowest priority,
    # heaviest dependency list, alphabetic tiebreak. The successor is the
    # next node in the cycle if it's still in the candidate's deps.
    candidate = max(
        core,
        key=lambda iid: (
            _PRIORITY_ORDER.get(id_to_issue.get(iid, {}).get("priority", "medium"), 1),
            len(id_to_issue.get(iid, {}).get("dependencies", [])),
            iid,
        ),
    )
    idx = core.index(candidate)
    successor = cycle[idx + 1]
    deps = list(id_to_issue.get(candidate, {}).get("dependencies", []))
    removed = False
    if successor in deps:
        deps.remove(successor)
        removed = True
    else:
        # The "expected" successor was already gone; drop the first dep
        # that points back into the cycle so we still make progress.
        for dep in deps:
            if dep in core:
                successor = dep
                deps.remove(dep)
                removed = True
                break
    if removed:
        id_to_issue[candidate]["dependencies"] = deps
        touched.add(candidate)
        logger.warning(
            f"Removed circular dependency edge during planning: {candidate} -> {successor}"
        )
    return removed


def sanitize_dependencies(
    issues: Iterable[dict],
    logger: logging.Logger,
) -> tuple[set[str], int]:
    """Normalize the dependency graph in place. Return (touched_ids, total_changes).

    Mutates each issue dict's ``dependencies`` list. Caller is responsible
    for persisting the touched issues (markdown sync, state.update_issue).
    """
    issues_list = [d for d in issues if d.get("id")]
    if not issues_list:
        return set(), 0

    id_to_issue = {d["id"]: d for d in issues_list}
    issue_ids = set(id_to_issue.keys())
    touched: set[str] = set()

    total_changes = _scrub_edges(id_to_issue, issue_ids, logger, touched)

    max_passes = max(1, len(id_to_issue) * 2)
    for _ in range(max_passes):
        cycles = _detect_cycles(id_to_issue)
        if not cycles:
            break
        for cycle in cycles:
            if _break_one_cycle(cycle, id_to_issue, logger, touched):
                total_changes += 1
    else:
        logger.error("Dependency sanitization exceeded max passes; unresolved cycles may remain.")

    return touched, total_changes
