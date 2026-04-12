"""Tests for aidlc.planner module."""

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from aidlc.planner import Planner
from aidlc.models import RunState, RunPhase


@pytest.fixture
def logger():
    return logging.getLogger("test_planner")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,  # Don't checkpoint during tests
        "max_consecutive_failures": 3,
        "finalization_budget_percent": 10,
        "dry_run": True,
        "max_planning_cycles": 2,
    }


@pytest.fixture
def state():
    s = RunState(run_id="test_plan", config_name="default")
    s.plan_budget_seconds = 3600.0
    return s


@pytest.fixture
def cli():
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "[DRY RUN]",
        "error": None,
        "failure_type": None,
        "duration_seconds": 0.0,
        "retries": 0,
    }
    return cli


class TestPlanner:
    def test_dry_run_completes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert state.phase in (RunPhase.PLANNING, RunPhase.PLAN_FINALIZATION)
        assert state.planning_cycles <= 2

    def test_cycle_cap_respected(self, state, config, cli, logger, tmp_path):
        config["max_planning_cycles"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert state.planning_cycles <= 1

    def test_budget_exhaustion_stops(self, state, config, cli, logger, tmp_path):
        state.plan_budget_seconds = 0.0  # Already exhausted
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert "exhausted" in (state.stop_reason or "").lower()

    def test_finalization_transition(self, state, config, cli, logger, tmp_path):
        state.plan_budget_seconds = 100.0
        state.plan_elapsed_seconds = 91.0  # Past 90% threshold
        config["max_planning_cycles"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert state.phase == RunPhase.PLAN_FINALIZATION

    def test_budget_exhaustion_runs_one_grace_finalization_cycle(
        self, state, config, cli, logger, tmp_path
    ):
        state.plan_budget_seconds = 100.0
        state.plan_elapsed_seconds = 80.0  # Below normal finalization threshold
        config["max_planning_cycles"] = 0
        config["planning_finalization_grace_cycles"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)

        phases_seen = []

        def fake_cycle():
            phases_seen.append(state.phase)
            # Simulate first planning cycle overrunning remaining budget.
            if len(phases_seen) == 1:
                state.plan_elapsed_seconds = 120.0
            return True

        planner._planning_cycle = fake_cycle
        planner.run()

        assert phases_seen == [RunPhase.PLANNING, RunPhase.PLAN_FINALIZATION]
        assert (state.stop_reason or "") == "Planning budget exhausted"

    def test_apply_create_issue(self, state, config, cli, logger, tmp_path):
        from aidlc.schemas import PlanningAction
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)

        action = PlanningAction(
            action_type="create_issue",
            rationale="Test",
            issue_id="ISSUE-001",
            title="Test Issue",
            description="Test description",
            priority="high",
            acceptance_criteria=["AC1"],
        )
        planner._apply_action(action)

        assert len(state.issues) == 1
        assert state.issues[0]["id"] == "ISSUE-001"
        assert state.issues_created == 1
        # Issue file should be written
        issue_file = Path(config["_issues_dir"]) / "ISSUE-001.md"
        assert issue_file.exists()

    def test_apply_update_issue(self, state, config, cli, logger, tmp_path):
        from aidlc.schemas import PlanningAction
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        # Create issues dir
        issues_dir = Path(config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)
        planner = Planner(state, run_dir, config, cli, "context", logger)

        # Create issue first (via planner so file exists)
        create_action = PlanningAction(
            action_type="create_issue",
            rationale="Need",
            issue_id="ISSUE-001",
            title="Original",
            description="Orig",
            acceptance_criteria=["AC1"],
        )
        planner._apply_action(create_action)

        action = PlanningAction(
            action_type="update_issue",
            rationale="Refine",
            issue_id="ISSUE-001",
            description="Updated description",
        )
        planner._apply_action(action)
        updated = state.get_issue("ISSUE-001")
        assert updated.description == "Updated description"

    def test_apply_create_doc(self, state, config, cli, logger, tmp_path):
        from aidlc.schemas import PlanningAction
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)

        action = PlanningAction(
            action_type="create_doc",
            rationale="Design",
            file_path="docs/design.md",
            content="# Design\nContent",
        )
        planner._apply_action(action)

        doc_path = tmp_path / "docs" / "design.md"
        assert doc_path.exists()
        assert state.files_created == 1
        assert len(state.created_artifacts) == 1
        assert state.created_artifacts[0]["type"] == "doc"
        assert state.created_artifacts[0]["action"] == "create"

    def test_consecutive_failures_stop(self, state, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": False,
            "output": "",
            "error": "fail",
            "failure_type": "issue",
            "duration_seconds": 0.0,
            "retries": 0,
        }
        config["max_consecutive_failures"] = 2
        config["max_planning_cycles"] = 10
        config["dry_run"] = False
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert "failures" in (state.stop_reason or "").lower()

    def test_planning_complete_ignored_when_not_offered(self, state, config, logger, tmp_path):
        """Claude's planning_complete is ignored until the system offers it."""
        cli = MagicMock()
        complete_response = json.dumps({
            "frontier_assessment": "All work captured",
            "planning_complete": True,
            "completion_reason": "All features captured",
            "actions": [],
            "cycle_notes": "Done",
        })
        cli.execute_prompt.return_value = {
            "success": True,
            "output": f"```json\n{complete_response}\n```",
            "error": None,
            "failure_type": None,
            "duration_seconds": 1.0,
            "retries": 0,
        }
        # With diminishing_returns_threshold=3, empty cycles will eventually trigger
        # the winding down detection after 3 cycles, then offer, then accept
        config["max_planning_cycles"] = 10
        config["dry_run"] = False
        config["diminishing_returns_threshold"] = 3
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        # Pre-seed an issue so issues_created > 0
        from aidlc.models import Issue
        issue = Issue(id="ISSUE-001", title="Existing", description="X", acceptance_criteria=["AC"])
        state.update_issue(issue)
        state.issues_created = 1
        doc_files = [
            {"path": "ARCHITECTURE.md", "content": "Architecture details " * 80, "priority": 0, "size": 1680},
            {"path": "DESIGN.md", "content": "Design details " * 80, "priority": 0, "size": 1120},
            {"path": "CLAUDE.md", "content": "Agent constraints " * 80, "priority": 0, "size": 1440},
        ]
        planner = Planner(state, run_dir, config, cli, "context", logger, doc_files=doc_files)
        planner.run()
        # Should NOT exit on cycle 1 — completion not offered yet
        assert state.planning_cycles > 1
        assert "complete" in (state.stop_reason or "").lower() or "clear" in (state.stop_reason or "").lower()
        assert "complete" in (state.stop_reason or "").lower()

    def test_planning_complete_deferred_until_winding_down(self, state, config, logger, tmp_path):
        """Claude's planning_complete is deferred — only honored after winding down confirmed."""
        cli = MagicMock()
        # Cycle 1: creates an issue (not winding down yet)
        create_response = json.dumps({
            "frontier_assessment": "Creating work",
            "actions": [{
                "action_type": "create_issue",
                "rationale": "Need this",
                "issue_id": "ISSUE-001",
                "title": "Real work",
                "description": "Do stuff",
                "priority": "high",
                "acceptance_criteria": ["Done"],
            }],
            "cycle_notes": "",
        })
        # Cycles 2-4: only updates (winding down)
        update_response = json.dumps({
            "frontier_assessment": "Minor update",
            "actions": [{
                "action_type": "update_issue",
                "rationale": "Polish",
                "issue_id": "ISSUE-001",
                "description": "Updated",
            }],
            "cycle_notes": "",
        })
        # Cycle 5: Claude declares complete after being offered the option
        complete_response = json.dumps({
            "frontier_assessment": "All done",
            "planning_complete": True,
            "completion_reason": "Plan is comprehensive",
            "actions": [],
            "cycle_notes": "",
        })
        cli.execute_prompt.side_effect = [
            {"success": True, "output": f"```json\n{create_response}\n```",
             "error": None, "failure_type": None, "duration_seconds": 1.0, "retries": 0},
            {"success": True, "output": f"```json\n{update_response}\n```",
             "error": None, "failure_type": None, "duration_seconds": 1.0, "retries": 0},
            {"success": True, "output": f"```json\n{update_response}\n```",
             "error": None, "failure_type": None, "duration_seconds": 1.0, "retries": 0},
            {"success": True, "output": f"```json\n{update_response}\n```",
             "error": None, "failure_type": None, "duration_seconds": 1.0, "retries": 0},
            {"success": True, "output": f"```json\n{complete_response}\n```",
             "error": None, "failure_type": None, "duration_seconds": 1.0, "retries": 0},
        ]
        config["max_planning_cycles"] = 100
        config["dry_run"] = False
        config["diminishing_returns_threshold"] = 3
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        issues_dir = Path(config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)

        doc_files = [
            {"path": "ARCHITECTURE.md", "content": "Architecture details " * 80, "priority": 0, "size": 1680},
            {"path": "DESIGN.md", "content": "Design details " * 80, "priority": 0, "size": 1120},
            {"path": "CLAUDE.md", "content": "Agent constraints " * 80, "priority": 0, "size": 1440},
        ]
        planner = Planner(state, run_dir, config, cli, "context", logger, doc_files=doc_files)
        planner.run()
        # Should run: 1 create + 3 updates (triggers offer) + 1 complete = 5 cycles
        # But cycle 5 returns empty actions + planning_complete -> frontier clear
        assert "complete" in (state.stop_reason or "").lower() or "clear" in (state.stop_reason or "").lower()

    def test_diminishing_returns_exits_early(self, state, config, logger, tmp_path):
        """Update-only cycles trigger offer, then force exit if Claude doesn't declare done."""
        cli = MagicMock()
        # Return a response with only update_issue actions (no new issues)
        update_response = json.dumps({
            "frontier_assessment": "Minor refinements",
            "actions": [{
                "action_type": "update_issue",
                "rationale": "Polish",
                "issue_id": "ISSUE-001",
                "description": "Updated description",
            }],
            "cycle_notes": "",
        })
        cli.execute_prompt.return_value = {
            "success": True,
            "output": f"```json\n{update_response}\n```",
            "error": None,
            "failure_type": None,
            "duration_seconds": 1.0,
            "retries": 0,
        }
        config["max_planning_cycles"] = 100
        config["dry_run"] = False
        config["diminishing_returns_threshold"] = 3

        # Pre-seed an existing issue so updates have something to target
        from aidlc.models import Issue
        issue = Issue(
            id="ISSUE-001", title="Existing", description="Exists",
            acceptance_criteria=["AC1"],
        )
        state.update_issue(issue)
        state.issues_created = 1

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        issues_dir = Path(config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)
        (issues_dir / "ISSUE-001.md").write_text("# ISSUE-001")

        doc_files = [
            {"path": "ARCHITECTURE.md", "content": "Architecture details " * 80, "priority": 0, "size": 1680},
            {"path": "DESIGN.md", "content": "Design details " * 80, "priority": 0, "size": 1120},
            {"path": "CLAUDE.md", "content": "Agent constraints " * 80, "priority": 0, "size": 1440},
        ]
        planner = Planner(state, run_dir, config, cli, "context", logger, doc_files=doc_files)
        planner.run()
        # 3 cycles to detect winding down + offer, then 2 more before force exit = 5
        assert state.planning_cycles == 5
        assert "no new issues" in (state.stop_reason or "").lower()
