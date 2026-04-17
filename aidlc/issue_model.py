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
        return issue
