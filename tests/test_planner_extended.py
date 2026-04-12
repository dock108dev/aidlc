"""Extended tests for aidlc.planner — targeting uncovered lines."""

import json
import logging
import pytest
from unittest.mock import MagicMock

from aidlc.planner import Planner
from aidlc.models import RunState, Issue
from aidlc.planner_helpers import write_planning_index


@pytest.fixture
def logger():
    return logging.getLogger("test_plan_ext")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,
        "max_consecutive_failures": 3,
        "finalization_budget_percent": 10,
        "dry_run": False,
        "max_planning_cycles": 0,
    }


def make_planning_response(actions=None, frontier="Assessed", notes="Notes"):
    """Build a valid planning JSON response."""
    data = {
        "frontier_assessment": frontier,
        "actions": actions or [],
        "cycle_notes": notes,
    }
    return f"```json\n{json.dumps(data)}\n```"


class TestPlanningCycleWithRealOutput:
    def test_creates_issues_from_claude_output(self, config, logger, tmp_path):
        response = make_planning_response(actions=[
            {
                "action_type": "create_issue",
                "rationale": "Need auth",
                "issue_id": "ISSUE-001",
                "title": "Add authentication",
                "description": "Implement auth module",
                "priority": "high",
                "labels": ["feature"],
                "dependencies": [],
                "acceptance_criteria": ["Login works", "Logout works"],
            },
            {
                "action_type": "create_issue",
                "rationale": "Need tests",
                "issue_id": "ISSUE-002",
                "title": "Add auth tests",
                "description": "Test auth module",
                "priority": "medium",
                "labels": ["test"],
                "dependencies": ["ISSUE-001"],
                "acceptance_criteria": ["All tests pass"],
            },
        ])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 5.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_planning_cycles"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert state.issues_created == 2
        assert len(state.issues) == 2
        assert state.issues[0]["id"] == "ISSUE-001"

    def test_empty_actions_eventually_stops_planning(self, config, logger, tmp_path):
        """Repeated empty cycles eventually stop planning via diminishing returns."""
        response = make_planning_response(actions=[])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        state.issues_created = 1
        config["max_planning_cycles"] = 20
        config["diminishing_returns_threshold"] = 3
        config["planning_doc_min_chars"] = 10
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        from aidlc.models import Issue
        issue = Issue(id="ISSUE-001", title="X", description="X", acceptance_criteria=["AC"])
        state.update_issue(issue)
        doc_files = [
            {"path": "ROADMAP.md", "content": "Phase 1\n- A\n- B", "priority": 0, "size": 16},
            {"path": "ARCHITECTURE.md", "content": "Components and flow", "priority": 0, "size": 19},
            {"path": "DESIGN.md", "content": "Patterns and conventions", "priority": 0, "size": 24},
            {"path": "CLAUDE.md", "content": "Agent rules and constraints", "priority": 0, "size": 27},
        ]
        planner = Planner(state, run_dir, config, cli, "context", logger, doc_files=doc_files)
        planner.run()
        assert state.planning_cycles > 1
        assert "clear" in (state.stop_reason or "").lower()

    def test_invalid_json_counts_as_failure(self, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": "Just some text with no JSON",
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_consecutive_failures"] = 1
        config["max_planning_cycles"] = 10
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert "failures" in (state.stop_reason or "").lower()

    def test_empty_actions_with_missing_docs_counts_as_failure(self, config, logger, tmp_path):
        response = make_planning_response(actions=[])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_consecutive_failures"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert "failures" in (state.stop_reason or "").lower()

    def test_validation_errors_fail_cycle(self, config, logger, tmp_path):
        response = make_planning_response(actions=[
            {
                "action_type": "create_issue",
                "rationale": "Need auth",
                "issue_id": "ISSUE-001",
                "title": "Add authentication",
                # Missing required description + acceptance_criteria
            },
        ])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_consecutive_failures"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert "failures" in (state.stop_reason or "").lower()

    def test_cycle_fails_when_all_actions_error(self, config, logger, tmp_path):
        response = make_planning_response(actions=[
            {
                "action_type": "create_issue",
                "rationale": "Need auth",
                "issue_id": "ISSUE-001",
                "title": "Add authentication",
                "description": "desc",
                "acceptance_criteria": ["AC1"],
            },
        ])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_consecutive_failures"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner._apply_action = MagicMock(side_effect=RuntimeError("disk full"))
        planner.run()
        assert "failures" in (state.stop_reason or "").lower()


class TestBuildPrompt:
    def test_includes_existing_issues(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        state.issues = [
            {"id": "ISSUE-001", "title": "Existing", "description": "D",
             "priority": "high", "labels": [], "dependencies": [],
             "acceptance_criteria": ["AC1"], "status": "pending",
             "implementation_notes": "", "verification_result": "",
             "files_changed": [], "attempt_count": 0, "max_attempts": 3},
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "project context", logger)
        prompt = planner._build_prompt(is_finalization=False)
        assert "ISSUE-001" in prompt
        assert "Existing" in prompt
        assert "Planning Task" in prompt

    def test_finalization_prompt(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 100
        state.plan_elapsed_seconds = 95
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        prompt = planner._build_prompt(is_finalization=True)
        assert "FINALIZATION" in prompt
        assert "MUST NOT" in prompt

    def test_normal_prompt_includes_instructions(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        prompt = planner._build_prompt(is_finalization=False)
        assert "Planning Mode" in prompt
        assert "acceptance criteria" in prompt.lower()

    def test_issue_context_is_bounded_for_large_backlog(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        state.issues = [
            {
                "id": f"ISSUE-{idx:03d}",
                "title": f"Issue title {idx}",
                "description": "D",
                "priority": "high" if idx % 7 == 0 else "medium",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": ["AC1"],
                "status": "pending",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 0,
                "max_attempts": 3,
            }
            for idx in range(1, 121)
        ]
        config["planning_issue_index_max_items"] = 25
        config["planning_issue_index_include_all_until"] = 20
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        prompt = planner._build_prompt(is_finalization=False)
        assert "Compact issue index (bounded for token control)." in prompt
        assert "Omitted from inline list" in prompt
        assert prompt.count("ISSUE-") < 60

    def test_planning_index_contains_full_status_and_category_rollups(
        self, config, logger, tmp_path
    ):
        state = RunState(run_id="test", config_name="default")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "Foundation setup",
                "description": "D",
                "priority": "high",
                "labels": ["infra", "backend"],
                "dependencies": [],
                "acceptance_criteria": ["AC1"],
                "status": "implemented",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 0,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-002",
                "title": "Add UI panel",
                "description": "D",
                "priority": "medium",
                "labels": ["frontend"],
                "dependencies": [],
                "acceptance_criteria": ["AC1"],
                "status": "pending",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 0,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-003",
                "title": "Fix sync bug",
                "description": "D",
                "priority": "high",
                "labels": ["backend"],
                "dependencies": [],
                "acceptance_criteria": ["AC1"],
                "status": "blocked",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 0,
                "max_attempts": 3,
            },
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        index_path = write_planning_index(planner)
        text = index_path.read_text()
        assert "## Issue Backlog Summary" in text
        assert "### Category Rollup (Labels)" in text
        assert "### Active Issues" in text
        assert "### Completed Issues" in text
        assert "Completion: 1/3" in text


class TestApplyActionEdgeCases:
    def test_update_unknown_issue_warns(self, config, logger, tmp_path):
        from aidlc.schemas import PlanningAction
        state = RunState(run_id="test", config_name="default")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        action = PlanningAction(
            action_type="update_issue",
            rationale="Refine",
            issue_id="ISSUE-999",
        )
        planner._apply_action(action)
        assert len(state.issues) == 0

    def test_update_doc(self, config, logger, tmp_path):
        from aidlc.schemas import PlanningAction
        state = RunState(run_id="test", config_name="default")
        # Create initial doc
        doc_path = tmp_path / "docs" / "design.md"
        doc_path.parent.mkdir(parents=True)
        doc_path.write_text("# V1")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        action = PlanningAction(
            action_type="update_doc",
            rationale="Update design",
            file_path="docs/design.md",
            content="# V2\nUpdated",
        )
        planner._apply_action(action)
        assert doc_path.read_text() == "# V2\nUpdated"
        assert state.files_created == 1
        assert state.created_artifacts[0]["action"] == "update"


class TestCheckpointDuringPlanning:
    def test_checkpoint_fires(self, config, logger, tmp_path):
        config["checkpoint_interval_minutes"] = 0  # Checkpoint every cycle
        config["max_planning_cycles"] = 1
        response = make_planning_response(actions=[
            {
                "action_type": "create_issue",
                "rationale": "Need it",
                "issue_id": "ISSUE-001",
                "title": "T",
                "description": "D",
                "priority": "high",
                "acceptance_criteria": ["AC"],
            },
        ])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600

        reports_dir = tmp_path / ".aidlc" / "reports" / "test"
        reports_dir.mkdir(parents=True)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        # Should have checkpointed
        assert state.checkpoint_count >= 1


class TestRenderIssueMd:
    def test_renders_complete_issue(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        issue = Issue(
            id="ISSUE-001", title="Test", description="Desc",
            priority="high", labels=["feature"],
            dependencies=["ISSUE-000"],
            acceptance_criteria=["AC1", "AC2"],
        )
        issue.implementation_notes = "Some notes"
        md = planner._render_issue_md(issue)
        assert "# ISSUE-001: Test" in md
        assert "high" in md
        assert "feature" in md
        assert "ISSUE-000" in md
        assert "- [ ] AC1" in md
        assert "Some notes" in md
