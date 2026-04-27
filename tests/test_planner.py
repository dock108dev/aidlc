"""Tests for aidlc.planner module."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aidlc.models import RunPhase, RunState
from aidlc.planner import Planner


@pytest.fixture
def logger():
    return logging.getLogger("test_planner")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(tmp_path / ".aidlc"),
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
            # Each cycle counts as productive work (issues_created bumps),
            # so the planner doesn't switch into verify mode and the budget
            # path is the one that triggers the stop.
            state.issues_created += 1
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
            issue_id="ISSUE-001",
            title="Original",
            description="Orig",
            acceptance_criteria=["AC1"],
        )
        planner._apply_action(create_action)

        action = PlanningAction(
            action_type="update_issue",
            issue_id="ISSUE-001",
            description="Updated description",
        )
        planner._apply_action(action)
        updated = state.get_issue("ISSUE-001")
        assert updated.description == "Updated description"

    def test_apply_update_issue_can_clear_dependencies(self, state, config, cli, logger, tmp_path):
        from aidlc.schemas import PlanningAction

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        issues_dir = Path(config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)
        planner = Planner(state, run_dir, config, cli, "context", logger)

        planner._apply_action(
            PlanningAction(
                action_type="create_issue",
                issue_id="ISSUE-001",
                title="Primary",
                description="Primary desc",
                acceptance_criteria=["AC1"],
            )
        )
        planner._apply_action(
            PlanningAction(
                action_type="create_issue",
                issue_id="ISSUE-002",
                title="Dep",
                description="Dep desc",
                acceptance_criteria=["AC1"],
            )
        )
        planner._apply_action(
            PlanningAction(
                action_type="update_issue",
                issue_id="ISSUE-001",
                dependencies=["ISSUE-002"],
            )
        )
        assert state.get_issue("ISSUE-001").dependencies == ["ISSUE-002"]

        planner._apply_action(
            PlanningAction(
                action_type="update_issue",
                issue_id="ISSUE-001",
                dependencies=[],
            )
        )
        assert state.get_issue("ISSUE-001").dependencies == []

    def test_sanitize_issue_dependencies_removes_invalid_and_breaks_cycle(
        self, state, config, cli, logger, tmp_path
    ):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        issues_dir = Path(config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)

        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "One",
                "description": "One",
                "priority": "high",
                "labels": [],
                "dependencies": ["ISSUE-001", "ISSUE-002", "ISSUE-002", "ISSUE-999"],
                "acceptance_criteria": ["AC1"],
                "status": "pending",
            },
            {
                "id": "ISSUE-002",
                "title": "Two",
                "description": "Two",
                "priority": "medium",
                "labels": [],
                "dependencies": ["ISSUE-001"],
                "acceptance_criteria": ["AC1"],
                "status": "pending",
            },
        ]
        planner = Planner(state, run_dir, config, cli, "context", logger)
        changes = planner._sanitize_issue_dependencies()
        assert changes > 0

        deps = {i["id"]: i.get("dependencies", []) for i in state.issues}
        assert "ISSUE-001" not in deps["ISSUE-001"]
        assert "ISSUE-999" not in deps["ISSUE-001"]
        assert deps["ISSUE-001"].count("ISSUE-002") <= 1
        assert not (
            "ISSUE-002" in deps.get("ISSUE-001", []) and "ISSUE-001" in deps.get("ISSUE-002", [])
        )

    def test_create_doc_action_type_rejected(self):
        """create_doc/update_doc actions are removed — schema validation must reject them."""
        from aidlc.schemas import PlanningAction

        action = PlanningAction(
            action_type="create_doc",
            issue_id=None,
        )
        errors = action.validate()
        assert any("Unknown action_type" in e for e in errors)

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
        complete_response = json.dumps(
            {
                "frontier_assessment": "All work captured",
                "planning_complete": True,
                "completion_reason": "All features captured",
                "actions": [],
                "cycle_notes": "Done",
            }
        )
        cli.execute_prompt.return_value = {
            "success": True,
            "output": f"```json\n{complete_response}\n```",
            "error": None,
            "failure_type": None,
            "duration_seconds": 1.0,
            "retries": 0,
        }
        # With planning_diminishing_returns_min_threshold=3, empty cycles will eventually trigger
        # the winding down detection after 3 cycles, then offer, then accept
        config["max_planning_cycles"] = 10
        config["dry_run"] = False
        config["planning_diminishing_returns_min_threshold"] = 3
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        # Pre-seed an issue so issues_created > 0
        from aidlc.models import Issue

        issue = Issue(
            id="ISSUE-001",
            title="Existing",
            description="X",
            acceptance_criteria=["AC"],
        )
        state.update_issue(issue)
        state.issues_created = 1
        doc_files = [
            {
                "path": "ARCHITECTURE.md",
                "content": "Architecture details " * 80,
                "priority": 0,
                "size": 1680,
            },
            {
                "path": "DESIGN.md",
                "content": "Design details " * 80,
                "priority": 0,
                "size": 1120,
            },
            {
                "path": "CLAUDE.md",
                "content": "Agent constraints " * 80,
                "priority": 0,
                "size": 1440,
            },
        ]
        planner = Planner(state, run_dir, config, cli, "context", logger, doc_files=doc_files)
        planner.run()
        # Should NOT exit on cycle 1 — completion not offered yet
        assert state.planning_cycles > 1
        assert (
            "complete" in (state.stop_reason or "").lower()
            or "clear" in (state.stop_reason or "").lower()
        )
        assert "complete" in (state.stop_reason or "").lower()

    def test_planning_complete_only_honored_in_verify_mode(self, state, config, logger, tmp_path):
        """The model's `planning_complete: true` is ignored on a normal cycle —
        the model sometimes adds it unprompted on greenfield repos. It is
        only honored on a verify cycle (which fires after a 0-new-issues
        cycle and is the explicit invitation for the model to declare done).

        Test sequence:
          - Cycle 1: create issue + planning_complete=true (UNPROMPTED — must
            be ignored). Cycle creates 1 new issue, so it's productive.
          - Cycle 2: empty actions → planner switches to verify mode.
          - Cycle 3 (verify mode): empty actions + planning_complete=true →
            now accepted because the verify prompt explicitly invites it.
        """
        cli = MagicMock()
        create_with_unprompted_complete = json.dumps(
            {
                "frontier_assessment": "Creating work",
                # The unprompted planning_complete on a NORMAL cycle.
                "planning_complete": True,
                "completion_reason": "Premature claim — should be ignored.",
                "actions": [
                    {
                        "action_type": "create_issue",
                        "issue_id": "ISSUE-001",
                        "title": "Real work",
                        "description": "Do stuff",
                        "priority": "high",
                        "acceptance_criteria": ["Done"],
                    }
                ],
                "cycle_notes": "",
            }
        )
        empty_response = json.dumps(
            {"frontier_assessment": "Nothing to add", "actions": [], "cycle_notes": ""}
        )
        verify_complete_response = json.dumps(
            {
                "frontier_assessment": "Coverage confirmed",
                "planning_complete": True,
                "completion_reason": "All BRAINDUMP intent covered by ISSUE-001",
                "actions": [],
                "cycle_notes": "",
            }
        )
        cli.execute_prompt.side_effect = [
            {
                "success": True,
                "output": f"```json\n{create_with_unprompted_complete}\n```",
                "error": None,
                "failure_type": None,
                "duration_seconds": 1.0,
                "retries": 0,
            },
            {
                "success": True,
                "output": f"```json\n{empty_response}\n```",
                "error": None,
                "failure_type": None,
                "duration_seconds": 1.0,
                "retries": 0,
            },
            {
                "success": True,
                "output": f"```json\n{verify_complete_response}\n```",
                "error": None,
                "failure_type": None,
                "duration_seconds": 1.0,
                "retries": 0,
            },
        ]
        config["max_planning_cycles"] = 100
        config["dry_run"] = False
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        issues_dir = Path(config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)

        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        # Cycle 1 (create) + cycle 2 (empty → verify) + cycle 3 (verify confirms) = 3 cycles.
        assert state.planning_cycles == 3
        # Verify-mode planning_complete reason is the one that lands.
        assert "ISSUE-001" in (state.stop_reason or "")
        assert "Premature claim" not in (state.stop_reason or "")

    def test_update_only_cycles_trigger_verify_then_stop(self, state, config, logger, tmp_path):
        """A cycle that proposes only update_issue actions (no new issues)
        is treated the same as an empty cycle — it triggers verify mode
        for the next cycle. If verify also produces no new issues,
        planning stops with the verify-confirmed reason. This prevents
        the planner from spinning forever on cosmetic updates."""
        cli = MagicMock()
        # Return a response with only update_issue actions (no new issues)
        update_response = json.dumps(
            {
                "frontier_assessment": "Minor refinements",
                "actions": [
                    {
                        "action_type": "update_issue",
                        "issue_id": "ISSUE-001",
                        "description": "Updated description",
                    }
                ],
                "cycle_notes": "",
            }
        )
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
        config["planning_diminishing_returns_min_threshold"] = 3

        # Pre-seed an existing issue so updates have something to target
        from aidlc.models import Issue

        issue = Issue(
            id="ISSUE-001",
            title="Existing",
            description="Exists",
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
            {
                "path": "ARCHITECTURE.md",
                "content": "Architecture details " * 80,
                "priority": 0,
                "size": 1680,
            },
            {
                "path": "DESIGN.md",
                "content": "Design details " * 80,
                "priority": 0,
                "size": 1120,
            },
            {
                "path": "CLAUDE.md",
                "content": "Agent constraints " * 80,
                "priority": 0,
                "size": 1440,
            },
        ]
        planner = Planner(state, run_dir, config, cli, "context", logger, doc_files=doc_files)
        planner.run()
        # Cycle 1: update_issue (0 new) → triggers verify mode for cycle 2.
        # Cycle 2 (verify): same update_issue (still 0 new) → planning stops.
        assert state.planning_cycles == 2
        assert "verify" in (state.stop_reason or "").lower()

    def test_update_only_works_when_no_new_issues_were_created_in_this_run(
        self, state, config, logger, tmp_path
    ):
        """Sanity: the verify-mode trigger fires on 0-new-issues regardless of
        whether the cycle's actions touch pre-existing or run-fresh issues.
        Pre-existing issues with only update_issue actions still count as
        zero-new-this-cycle and should land in verify mode just the same."""
        cli = MagicMock()
        update_response = json.dumps(
            {
                "frontier_assessment": "Minor refinements",
                "actions": [
                    {
                        "action_type": "update_issue",
                        "issue_id": "ISSUE-001",
                        "description": "Updated description",
                    }
                ],
                "cycle_notes": "",
            }
        )
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
        config["planning_diminishing_returns_min_threshold"] = 2

        from aidlc.models import Issue

        issue = Issue(
            id="ISSUE-001",
            title="Existing",
            description="Exists",
            acceptance_criteria=["AC1"],
        )
        state.update_issue(issue)
        state.issues_created = 0

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        issues_dir = Path(config["_issues_dir"])
        issues_dir.mkdir(parents=True, exist_ok=True)
        (issues_dir / "ISSUE-001.md").write_text("# ISSUE-001")

        doc_files = [
            {
                "path": "ARCHITECTURE.md",
                "content": "Architecture details " * 80,
                "priority": 0,
                "size": 1680,
            },
            {
                "path": "DESIGN.md",
                "content": "Design details " * 80,
                "priority": 0,
                "size": 1120,
            },
            {
                "path": "CLAUDE.md",
                "content": "Agent constraints " * 80,
                "priority": 0,
                "size": 1440,
            },
        ]
        planner = Planner(state, run_dir, config, cli, "context", logger, doc_files=doc_files)
        planner.run()
        # Same as above: 2 cycles before verify-mode confirms.
        assert state.planning_cycles == 2
        assert "verify" in (state.stop_reason or "").lower()
