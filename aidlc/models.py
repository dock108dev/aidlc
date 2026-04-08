"""Data models for AIDLC runner state."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RunStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"


class RunPhase(Enum):
    INIT = "init"
    AUDITING = "auditing"
    SCANNING = "scanning"
    PLANNING = "planning"
    PLAN_FINALIZATION = "plan_finalization"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    REPORTING = "reporting"
    DONE = "done"


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


@dataclass
class RunState:
    """Full state of an AIDLC run."""
    run_id: str
    config_name: str
    project_root: str = ""
    status: RunStatus = RunStatus.PENDING
    phase: RunPhase = RunPhase.INIT
    started_at: Optional[str] = None
    last_updated: Optional[str] = None

    # Time tracking
    elapsed_seconds: float = 0.0
    wall_clock_seconds: float = 0.0
    plan_budget_seconds: float = 14400.0  # 4 hours default
    plan_elapsed_seconds: float = 0.0

    # Planning stats
    planning_cycles: int = 0
    issues_created: int = 0
    docs_scanned: int = 0
    files_created: int = 0

    # Implementation stats
    implementation_cycles: int = 0
    issues_implemented: int = 0
    issues_verified: int = 0
    issues_failed: int = 0
    total_issues: int = 0

    # Issue tracking
    issues: list = field(default_factory=list)  # list of Issue dicts
    current_issue_id: Optional[str] = None

    # Artifacts — each entry is {"path": str, "type": "doc"|"issue", "action": "create"|"update"}
    created_artifacts: list = field(default_factory=list)
    scanned_docs: list = field(default_factory=list)
    project_context: str = ""

    # Audit
    audit_depth: str = "none"  # none, quick, full
    audit_conflicts: list = field(default_factory=list)
    audit_completed: bool = False

    # Control
    checkpoint_count: int = 0
    stop_reason: Optional[str] = None
    notes: str = ""
    validation_results: list = field(default_factory=list)

    def is_plan_budget_exhausted(self) -> bool:
        return self.plan_elapsed_seconds >= self.plan_budget_seconds

    def should_finalize_planning(self, finalization_budget_percent: int = 10) -> bool:
        threshold = 1.0 - (finalization_budget_percent / 100.0)
        return self.plan_elapsed_seconds >= (self.plan_budget_seconds * threshold)

    def get_issue(self, issue_id: str) -> Optional[Issue]:
        for d in self.issues:
            if d["id"] == issue_id:
                return Issue.from_dict(d)
        return None

    def update_issue(self, issue: Issue) -> None:
        for i, d in enumerate(self.issues):
            if d["id"] == issue.id:
                self.issues[i] = issue.to_dict()
                return
        self.issues.append(issue.to_dict())

    def get_pending_issues(self) -> list[Issue]:
        """Get issues ready for implementation (deps met, not done)."""
        done_ids = {
            d["id"] for d in self.issues
            if d.get("status") in ("implemented", "verified")
        }
        pending = []
        for d in self.issues:
            if d.get("status") not in ("pending", "failed"):
                continue
            issue = Issue.from_dict(d)
            if issue.attempt_count >= issue.max_attempts:
                continue
            deps_met = all(dep in done_ids for dep in issue.dependencies)
            if deps_met:
                pending.append(issue)
        return pending

    def all_issues_resolved(self) -> bool:
        """True when every issue is implemented, verified, or skipped."""
        for d in self.issues:
            if d.get("status") in ("pending", "in_progress", "blocked"):
                return False
            if d.get("status") == "failed":
                issue = Issue.from_dict(d)
                if issue.attempt_count < issue.max_attempts:
                    return False
        return len(self.issues) > 0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "config_name": self.config_name,
            "project_root": self.project_root,
            "status": self.status.value,
            "phase": self.phase.value,
            "started_at": self.started_at,
            "last_updated": self.last_updated,
            "elapsed_seconds": self.elapsed_seconds,
            "wall_clock_seconds": self.wall_clock_seconds,
            "plan_budget_seconds": self.plan_budget_seconds,
            "plan_elapsed_seconds": self.plan_elapsed_seconds,
            "planning_cycles": self.planning_cycles,
            "issues_created": self.issues_created,
            "docs_scanned": self.docs_scanned,
            "files_created": self.files_created,
            "implementation_cycles": self.implementation_cycles,
            "issues_implemented": self.issues_implemented,
            "issues_verified": self.issues_verified,
            "issues_failed": self.issues_failed,
            "total_issues": self.total_issues,
            "issues": self.issues,
            "current_issue_id": self.current_issue_id,
            "created_artifacts": self.created_artifacts,
            "scanned_docs": self.scanned_docs,
            "project_context": self.project_context,
            "audit_depth": self.audit_depth,
            "audit_conflicts": self.audit_conflicts,
            "audit_completed": self.audit_completed,
            "checkpoint_count": self.checkpoint_count,
            "stop_reason": self.stop_reason,
            "notes": self.notes,
            "validation_results": self.validation_results,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunState":
        state = cls(
            run_id=data["run_id"],
            config_name=data["config_name"],
        )
        state.project_root = data.get("project_root", "")
        state.status = RunStatus(data.get("status", "pending"))
        state.phase = RunPhase(data.get("phase", "init"))
        state.started_at = data.get("started_at")
        state.last_updated = data.get("last_updated")
        state.elapsed_seconds = data.get("elapsed_seconds", 0.0)
        state.wall_clock_seconds = data.get("wall_clock_seconds", 0.0)
        state.plan_budget_seconds = data.get("plan_budget_seconds", 14400.0)
        state.plan_elapsed_seconds = data.get("plan_elapsed_seconds", 0.0)
        state.planning_cycles = data.get("planning_cycles", 0)
        state.issues_created = data.get("issues_created", 0)
        state.docs_scanned = data.get("docs_scanned", 0)
        state.files_created = data.get("files_created", 0)
        state.implementation_cycles = data.get("implementation_cycles", 0)
        state.issues_implemented = data.get("issues_implemented", 0)
        state.issues_verified = data.get("issues_verified", 0)
        state.issues_failed = data.get("issues_failed", 0)
        state.total_issues = data.get("total_issues", 0)
        state.issues = data.get("issues", [])
        state.current_issue_id = data.get("current_issue_id")
        state.created_artifacts = data.get("created_artifacts", [])
        state.scanned_docs = data.get("scanned_docs", [])
        state.project_context = data.get("project_context", "")
        state.audit_depth = data.get("audit_depth", "none")
        state.audit_conflicts = data.get("audit_conflicts", [])
        state.audit_completed = data.get("audit_completed", False)
        state.checkpoint_count = data.get("checkpoint_count", 0)
        state.stop_reason = data.get("stop_reason")
        state.notes = data.get("notes", "")
        state.validation_results = data.get("validation_results", [])
        return state
