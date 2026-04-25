"""Work-item (issue) model used by planning and implementation."""

from dataclasses import dataclass, field
from enum import Enum


class IssueStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


def issue_progress_rank(status: IssueStatus) -> int:
    """Monotonic completion level: higher means further along (for merge/hydration)."""
    order = {
        IssueStatus.PENDING: 0,
        IssueStatus.IN_PROGRESS: 1,
        IssueStatus.FAILED: 2,
        IssueStatus.BLOCKED: 2,
        IssueStatus.IMPLEMENTED: 3,
        IssueStatus.VERIFIED: 4,
        IssueStatus.SKIPPED: 5,
    }
    return order.get(status, 0)


# ISSUE-012: failure cause taxonomy. Distinguishes transient causes
# (auto-reopen on next cycle) from real-blocker causes (manual review).
FAILURE_CAUSE_TOKEN_EXHAUSTED = "failed_token_exhausted"
FAILURE_CAUSE_DEPENDENCY = "failed_dependency"
FAILURE_CAUSE_TEST_REGRESSION = "failed_test_regression"
FAILURE_CAUSE_UNKNOWN = "failed_unknown"

# Causes considered transient — auto-reopened to PENDING on a fresh
# implementation cycle (or always with --retry-failed).
TRANSIENT_FAILURE_CAUSES = frozenset(
    {FAILURE_CAUSE_TOKEN_EXHAUSTED, FAILURE_CAUSE_UNKNOWN}
)


@dataclass
class Issue:
    """A single work item created during planning."""

    id: str
    title: str
    description: str
    priority: str = "medium"  # high, medium, low
    labels: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    acceptance_criteria: list = field(default_factory=list)
    status: IssueStatus = IssueStatus.PENDING
    implementation_notes: str = ""
    verification_result: str = ""
    files_changed: list = field(default_factory=list)
    attempt_count: int = 0
    max_attempts: int = 3
    # ISSUE-012: optional cause set when status flips to FAILED. Used to
    # distinguish transient (token exhaustion) from real blockers (dep/test
    # regression). None when not failed.
    failure_cause: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "labels": self.labels,
            "dependencies": self.dependencies,
            "acceptance_criteria": self.acceptance_criteria,
            "status": self.status.value,
            "implementation_notes": self.implementation_notes,
            "verification_result": self.verification_result,
            "files_changed": self.files_changed,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "failure_cause": self.failure_cause,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Issue":
        issue = cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            priority=data.get("priority", "medium"),
            labels=data.get("labels", []),
            dependencies=data.get("dependencies", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
        )
        issue.status = IssueStatus(data.get("status", "pending"))
        issue.implementation_notes = data.get("implementation_notes", "")
        issue.verification_result = data.get("verification_result", "")
        issue.files_changed = data.get("files_changed", [])
        issue.attempt_count = data.get("attempt_count", 0)
        issue.max_attempts = data.get("max_attempts", 3)
        issue.failure_cause = data.get("failure_cause")
        return issue
