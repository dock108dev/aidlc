"""Data models for AIDLC runner state."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .issue_model import Issue, IssueStatus


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
    VALIDATING = "validating"
    FINALIZING = "finalizing"
    REPORTING = "reporting"
    DONE = "done"


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

    # Time tracking — elapsed_seconds = Claude CLI subprocess (execute_prompt); console_seconds = local shells
    elapsed_seconds: float = 0.0
    console_seconds: float = 0.0
    plan_budget_seconds: float = 14400.0  # 4 hours default
    plan_elapsed_seconds: float = 0.0

    # Claude usage telemetry
    claude_calls_total: int = 0
    claude_calls_succeeded: int = 0
    claude_calls_failed: int = 0
    claude_retries_total: int = 0
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0
    claude_cache_creation_input_tokens: int = 0
    claude_cache_read_input_tokens: int = 0
    claude_total_input_tokens: int = 0
    claude_total_tokens: int = 0
    claude_web_search_requests: int = 0
    claude_web_fetch_requests: int = 0
    claude_cost_usd_exact: float = 0.0
    claude_cost_usd_estimated: float = 0.0
    claude_estimated_cost_calls: int = 0
    claude_exact_cost_calls: int = 0
    claude_model_usage: dict = field(default_factory=dict)

    # Multi-provider telemetry (Phase 1+)
    # provider_account_usage: {provider_id: {account_id: {calls, tokens, cost_usd_exact, cost_usd_estimated}}}
    provider_account_usage: dict = field(default_factory=dict)
    # phase_usage: {phase_name: {provider_id, account_id, model, calls, input_tokens, output_tokens, cost_usd_exact, cost_usd_estimated}}
    phase_usage: dict = field(default_factory=dict)
    # routing_decisions: list of {phase, provider_id, account_id, model, reasoning, strategy, fallback}
    routing_decisions: list = field(default_factory=list)

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

    # Validation loop
    validation_cycles: int = 0
    validation_issues_created: int = 0
    validation_test_results: list = field(default_factory=list)

    # Finalization
    finalize_passes_completed: list = field(default_factory=list)
    finalize_passes_requested: list = field(default_factory=list)

    # Control
    checkpoint_count: int = 0
    stop_reason: Optional[str] = None
    notes: str = ""
    validation_results: list = field(default_factory=list)
    # After accepting pre-existing full-suite debt, use narrower tests during implementation.
    project_wide_tests_unstable: bool = False

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
        done_ids = {d["id"] for d in self.issues if d.get("status") in ("implemented", "verified")}
        pending = []
        for d in self.issues:
            if d.get("status") not in ("pending", "failed", "in_progress"):
                continue
            issue = Issue.from_dict(d)
            if issue.attempt_count >= issue.max_attempts:
                continue
            deps_met = all(dep in done_ids for dep in issue.dependencies)
            if deps_met:
                pending.append(issue)
        pending.sort(
            key=lambda i: (0 if i.status == IssueStatus.IN_PROGRESS else 1, i.id),
        )
        return pending

    def all_issues_resolved(self) -> bool:
        """True when every issue is terminal for this lifecycle (verified/skipped) or exhausted.

        ``implemented`` is *not* terminal: those issues still need the final verification pass
        to be promoted to verified; otherwise the implementation loop is skipped early.
        """
        for d in self.issues:
            if d.get("status") in ("pending", "in_progress", "blocked", "implemented"):
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
            "console_seconds": self.console_seconds,
            "plan_budget_seconds": self.plan_budget_seconds,
            "plan_elapsed_seconds": self.plan_elapsed_seconds,
            "claude_calls_total": self.claude_calls_total,
            "claude_calls_succeeded": self.claude_calls_succeeded,
            "claude_calls_failed": self.claude_calls_failed,
            "claude_retries_total": self.claude_retries_total,
            "claude_input_tokens": self.claude_input_tokens,
            "claude_output_tokens": self.claude_output_tokens,
            "claude_cache_creation_input_tokens": self.claude_cache_creation_input_tokens,
            "claude_cache_read_input_tokens": self.claude_cache_read_input_tokens,
            "claude_total_input_tokens": self.claude_total_input_tokens,
            "claude_total_tokens": self.claude_total_tokens,
            "claude_web_search_requests": self.claude_web_search_requests,
            "claude_web_fetch_requests": self.claude_web_fetch_requests,
            "claude_cost_usd_exact": self.claude_cost_usd_exact,
            "claude_cost_usd_estimated": self.claude_cost_usd_estimated,
            "claude_estimated_cost_calls": self.claude_estimated_cost_calls,
            "claude_exact_cost_calls": self.claude_exact_cost_calls,
            "claude_model_usage": self.claude_model_usage,
            "provider_account_usage": self.provider_account_usage,
            "phase_usage": self.phase_usage,
            "routing_decisions": self.routing_decisions,
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
            "validation_cycles": self.validation_cycles,
            "validation_issues_created": self.validation_issues_created,
            "validation_test_results": self.validation_test_results,
            "finalize_passes_completed": self.finalize_passes_completed,
            "finalize_passes_requested": self.finalize_passes_requested,
            "checkpoint_count": self.checkpoint_count,
            "stop_reason": self.stop_reason,
            "notes": self.notes,
            "validation_results": self.validation_results,
            "project_wide_tests_unstable": self.project_wide_tests_unstable,
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
        state.console_seconds = data.get("console_seconds", 0.0)
        state.plan_budget_seconds = data.get("plan_budget_seconds", 14400.0)
        state.plan_elapsed_seconds = data.get("plan_elapsed_seconds", 0.0)
        state.claude_calls_total = data.get("claude_calls_total", 0)
        state.claude_calls_succeeded = data.get("claude_calls_succeeded", 0)
        state.claude_calls_failed = data.get("claude_calls_failed", 0)
        state.claude_retries_total = data.get("claude_retries_total", 0)
        state.claude_input_tokens = data.get("claude_input_tokens", 0)
        state.claude_output_tokens = data.get("claude_output_tokens", 0)
        state.claude_cache_creation_input_tokens = data.get("claude_cache_creation_input_tokens", 0)
        state.claude_cache_read_input_tokens = data.get("claude_cache_read_input_tokens", 0)
        state.claude_total_input_tokens = data.get("claude_total_input_tokens", 0)
        state.claude_total_tokens = data.get("claude_total_tokens", 0)
        state.claude_web_search_requests = data.get("claude_web_search_requests", 0)
        state.claude_web_fetch_requests = data.get("claude_web_fetch_requests", 0)
        state.claude_cost_usd_exact = data.get("claude_cost_usd_exact", 0.0)
        state.claude_cost_usd_estimated = data.get("claude_cost_usd_estimated", 0.0)
        state.claude_estimated_cost_calls = data.get("claude_estimated_cost_calls", 0)
        state.claude_exact_cost_calls = data.get("claude_exact_cost_calls", 0)
        state.claude_model_usage = data.get("claude_model_usage", {})
        state.provider_account_usage = data.get("provider_account_usage", {})
        state.phase_usage = data.get("phase_usage", {})
        state.routing_decisions = data.get("routing_decisions", [])
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
        state.validation_cycles = data.get("validation_cycles", 0)
        state.validation_issues_created = data.get("validation_issues_created", 0)
        state.validation_test_results = data.get("validation_test_results", [])
        state.finalize_passes_completed = data.get("finalize_passes_completed", [])
        state.finalize_passes_requested = data.get("finalize_passes_requested", [])
        state.checkpoint_count = data.get("checkpoint_count", 0)
        state.stop_reason = data.get("stop_reason")
        state.notes = data.get("notes", "")
        state.validation_results = data.get("validation_results", [])
        state.project_wide_tests_unstable = bool(data.get("project_wide_tests_unstable", False))
        return state

    def record_provider_result(
        self,
        result: dict,
        config: dict | None = None,
        phase: str | None = None,
    ) -> None:
        """Accumulate telemetry from any provider result payload."""
        self.claude_calls_total += 1
        if result.get("success"):
            self.claude_calls_succeeded += 1
        else:
            self.claude_calls_failed += 1

        retries = int(result.get("retries", 0) or 0)
        self.claude_retries_total += retries

        usage = result.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
        web_search_requests = int(usage.get("web_search_requests", 0) or 0)
        web_fetch_requests = int(usage.get("web_fetch_requests", 0) or 0)
        total_input_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
        total_tokens = total_input_tokens + output_tokens

        self.claude_input_tokens += input_tokens
        self.claude_output_tokens += output_tokens
        self.claude_cache_creation_input_tokens += cache_creation_tokens
        self.claude_cache_read_input_tokens += cache_read_tokens
        self.claude_total_input_tokens += total_input_tokens
        self.claude_total_tokens += total_tokens
        self.claude_web_search_requests += web_search_requests
        self.claude_web_fetch_requests += web_fetch_requests

        model = str(result.get("model_used") or "unknown")
        model_usage = self.claude_model_usage.setdefault(
            model,
            {
                "calls": 0,
                "success": 0,
                "failed": 0,
                "retries": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "total_input_tokens": 0,
                "total_tokens": 0,
                "web_search_requests": 0,
                "web_fetch_requests": 0,
                "cost_usd_exact": 0.0,
                "cost_usd_estimated": 0.0,
            },
        )
        model_usage["calls"] += 1
        model_usage["success"] += 1 if result.get("success") else 0
        model_usage["failed"] += 0 if result.get("success") else 1
        model_usage["retries"] += retries
        model_usage["input_tokens"] += input_tokens
        model_usage["output_tokens"] += output_tokens
        model_usage["cache_creation_input_tokens"] += cache_creation_tokens
        model_usage["cache_read_input_tokens"] += cache_read_tokens
        model_usage["total_input_tokens"] += total_input_tokens
        model_usage["total_tokens"] += total_tokens
        model_usage["web_search_requests"] += web_search_requests
        model_usage["web_fetch_requests"] += web_fetch_requests

        cost_mode = "auto"
        cfg = config if isinstance(config, dict) else {}
        cost_mode = str(cfg.get("telemetry_cost_mode", "auto") or "auto").lower()
        # API-style $/M estimates are not subscription bills (Copilot/Codex flat plans). Off unless opted in.
        estimate_usd_enabled = bool(cfg.get("telemetry_estimate_usd", False))

        exact_cost = result.get("total_cost_usd")
        exact_cost_value = None
        try:
            exact_cost_value = float(exact_cost) if exact_cost is not None else None
        except (TypeError, ValueError):
            exact_cost_value = None

        should_track_exact = cost_mode in ("auto", "exact_only")
        # In auto mode, estimate from tokens unless we have positive exact billing from the CLI.
        # (Some adapters return total_cost_usd=0 when billing is unknown — still estimate.)
        has_positive_exact = exact_cost_value is not None and exact_cost_value > 0
        if cost_mode == "estimate_only":
            should_track_estimated = True
        elif cost_mode == "exact_only":
            should_track_estimated = False
        elif not estimate_usd_enabled:
            should_track_estimated = False
        else:
            should_track_estimated = not has_positive_exact

        if should_track_exact and exact_cost_value is not None:
            self.claude_cost_usd_exact += exact_cost_value
            self.claude_exact_cost_calls += 1
            model_usage["cost_usd_exact"] += exact_cost_value

        if should_track_estimated:
            estimated_cost = self._estimate_usage_cost(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens,
                config=config or {},
            )
            self.claude_cost_usd_estimated += estimated_cost
            self.claude_estimated_cost_calls += 1
            model_usage["cost_usd_estimated"] += estimated_cost

        provider_id = str(result.get("provider_id") or "claude")
        account_id = str(result.get("account_id") or "default")

        # Per-provider/account usage
        prov_map = self.provider_account_usage.setdefault(provider_id, {})
        acc_map = prov_map.setdefault(
            account_id,
            {
                "calls": 0,
                "calls_succeeded": 0,
                "calls_failed": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd_exact": 0.0,
                "cost_usd_estimated": 0.0,
            },
        )
        acc_map["calls"] += 1
        acc_map["calls_succeeded"] += 1 if result.get("success") else 0
        acc_map["calls_failed"] += 0 if result.get("success") else 1
        acc_map["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
        acc_map["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
        acc_map["total_tokens"] += (
            input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens
        )
        if should_track_estimated:
            acc_map["cost_usd_estimated"] += estimated_cost
        cost_exact = result.get("total_cost_usd")
        if cost_exact is not None:
            try:
                acc_map["cost_usd_exact"] += float(cost_exact)
            except (TypeError, ValueError):
                pass

        # Per-phase usage
        if phase:
            phase_entry = self.phase_usage.setdefault(
                phase,
                {
                    "provider_id": provider_id,
                    "account_id": account_id,
                    "model": str(result.get("model_used") or "unknown"),
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd_exact": 0.0,
                },
            )
            phase_entry["calls"] += 1
            phase_entry["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
            phase_entry["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
            if cost_exact is not None:
                try:
                    phase_entry["cost_usd_exact"] += float(cost_exact)
                except (TypeError, ValueError):
                    pass

        # Record routing decision if present
        routing = result.get("routing_decision")
        if isinstance(routing, dict):
            self.routing_decisions.append(routing)

    @staticmethod
    def _estimate_usage_cost(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int,
        cache_read_tokens: int,
        config: dict,
    ) -> float:
        """Estimate cost using configurable per-model rates (USD per million tokens)."""
        pricing = config.get("telemetry_model_pricing_usd_per_million_tokens", {})
        if not isinstance(pricing, dict):
            pricing = {}

        model_key = str(model or "").lower()
        selected = pricing.get(model_key)
        if not isinstance(selected, dict):
            if "opus" in model_key and isinstance(pricing.get("opus"), dict):
                selected = pricing.get("opus")
            elif "sonnet" in model_key and isinstance(pricing.get("sonnet"), dict):
                selected = pricing.get("sonnet")
            elif "haiku" in model_key and isinstance(pricing.get("haiku"), dict):
                selected = pricing.get("haiku")
            else:
                selected = pricing.get("default")
        if not isinstance(selected, dict):
            selected = {}

        input_rate = float(selected.get("input", 0.0) or 0.0)
        output_rate = float(selected.get("output", 0.0) or 0.0)
        cache_creation_rate = float(selected.get("cache_creation_input", input_rate * 1.25) or 0.0)
        cache_read_rate = float(selected.get("cache_read_input", input_rate * 0.10) or 0.0)

        return (
            (input_tokens / 1_000_000.0) * input_rate
            + ((output_tokens / 1_000_000.0) * output_rate)
            + ((cache_creation_tokens / 1_000_000.0) * cache_creation_rate)
            + ((cache_read_tokens / 1_000_000.0) * cache_read_rate)
        )
