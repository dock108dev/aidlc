"""Tests for aidlc.planner module."""

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aidlc.planner import Planner
from aidlc.models import RunState, RunPhase, IssueStatus


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
        from aidlc.models import Issue
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

    def test_planning_complete_signal(self, state, config, logger, tmp_path):
        """Claude declares planning_complete: true -> planner exits early."""
        cli = MagicMock()
        complete_response = json.dumps({
            "frontier_assessment": "All work captured",
            "planning_complete": True,
            "completion_reason": "All features from ROADMAP.md have been captured as issues",
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
        config["max_planning_cycles"] = 100
        config["dry_run"] = False
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert state.planning_cycles == 1
        assert "complete" in (state.stop_reason or "").lower()

    def test_planning_complete_with_final_actions(self, state, config, logger, tmp_path):
        """Claude can declare complete while still submitting final refinements."""
        cli = MagicMock()
        complete_response = json.dumps({
            "frontier_assessment": "Final cleanup",
            "planning_complete": True,
            "completion_reason": "Plan is comprehensive",
            "actions": [{
                "action_type": "create_issue",
                "rationale": "Last issue",
                "issue_id": "ISSUE-001",
                "title": "Final cleanup task",
                "description": "Clean up",
                "priority": "low",
                "acceptance_criteria": ["Code is clean"],
            }],
            "cycle_notes": "",
        })
        cli.execute_prompt.return_value = {
            "success": True,
            "output": f"```json\n{complete_response}\n```",
            "error": None,
            "failure_type": None,
            "duration_seconds": 1.0,
            "retries": 0,
        }
        config["max_planning_cycles"] = 100
        config["dry_run"] = False
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert state.planning_cycles == 1
        assert state.issues_created == 1  # Final action was applied
        assert "complete" in (state.stop_reason or "").lower()

    def test_diminishing_returns_exits_early(self, state, config, logger, tmp_path):
        """3 consecutive cycles with only updates (no new issues) -> exit."""
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

        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert state.planning_cycles == 3  # Exits after 3 update-only cycles
        assert "diminishing" in (state.stop_reason or "").lower() or "no new issues" in (state.stop_reason or "").lower()
