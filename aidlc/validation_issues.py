"""Generate fix issues from test failures for the validation loop."""

from .models import Issue
from .test_parser import TestFailure


def create_fix_issues(
    failures: list[TestFailure],
    existing_issue_ids: set[str],
    max_issues: int = 10,
    base_id_counter: int = 1,
) -> list[Issue]:
    """Convert test failures into AIDLC Issues for the implementer.

    Args:
        failures: Parsed test failures
        existing_issue_ids: IDs already in state (for dedup and numbering)
        max_issues: Cap on issues to create per validation cycle
        base_id_counter: Starting number for VFIX-NNN IDs

    Returns:
        List of Issue objects ready to add to state
    """
    issues = []
    seen_tests = set()

    for failure in failures:
        if len(issues) >= max_issues:
            break

        # Dedup: skip if we've already seen this test name
        if failure.test_name in seen_tests:
            continue
        seen_tests.add(failure.test_name)

        # Skip if a fix issue already exists for this test
        issue_id = f"VFIX-{base_id_counter + len(issues):03d}"
        if issue_id in existing_issue_ids:
            continue

        description = _build_description(failure)
        acceptance_criteria = [
            f"Test `{failure.test_name}` passes",
            "No new test failures introduced by the fix",
        ]

        issue = Issue(
            id=issue_id,
            title=f"Fix: {failure.short_description()}"[:120],
            description=description,
            priority="high",
            labels=["validation", "auto-generated", "test-fix"],
            acceptance_criteria=acceptance_criteria,
        )
        issues.append(issue)

    return issues


def _build_description(failure: TestFailure) -> str:
    """Build a detailed issue description from a test failure."""
    parts = [
        f"Test `{failure.test_name}` is failing and needs to be fixed.",
        "",
    ]

    if failure.file:
        loc = f"`{failure.file}"
        if failure.line:
            loc += f":{failure.line}"
        loc += "`"
        parts.append(f"**Location:** {loc}")

    if failure.assertion:
        parts.append(f"\n**Error:**\n```\n{failure.assertion}\n```")

    if failure.stack_trace:
        parts.append(f"\n**Stack trace:**\n```\n{failure.stack_trace}\n```")

    parts.extend([
        "",
        "**Instructions:**",
        "- Read the test to understand what it expects",
        "- Read the implementation code at the location above",
        "- Fix the root cause — do not modify the test unless the test itself is wrong",
        "- Ensure the fix doesn't break other tests",
    ])

    return "\n".join(parts)
