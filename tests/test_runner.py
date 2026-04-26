"""Tests for aidlc.runner module."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from aidlc.issue_model import Issue
from aidlc.models import IssueStatus, RunPhase, RunState
from aidlc.runner import hydrate_existing_issues, init_run, run_full, scan_project


@pytest.fixture
def config(tmp_path):
    aidlc_dir = tmp_path / ".aidlc"
    aidlc_dir.mkdir()
    (aidlc_dir / "issues").mkdir()
    (aidlc_dir / "runs").mkdir()
    (aidlc_dir / "reports").mkdir()

    return {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(aidlc_dir),
        "_runs_dir": str(aidlc_dir / "runs"),
        "_reports_dir": str(aidlc_dir / "reports"),
        "_issues_dir": str(aidlc_dir / "issues"),
        "providers": {
            "claude": {
                "enabled": True,
                "cli_command": "claude",
                "default_model": "sonnet",
            }
        },
        "plan_budget_hours": 0.01,
        "checkpoint_interval_minutes": 999,
        "dry_run": True,
        "claude_hard_timeout_seconds": 10,
        "retry_max_attempts": 0,
        "retry_base_delay_seconds": 0.01,
        "retry_max_delay_seconds": 0.05,
        "retry_backoff_factor": 2.0,
        "max_consecutive_failures": 3,
        "finalization_budget_percent": 10,
        "max_implementation_attempts": 3,
        "max_planning_cycles": 1,
        "max_implementation_cycles": 1,
        "test_timeout_seconds": 30,
        "max_doc_chars": 10000,
        "max_context_chars": 80000,
        "max_implementation_context_chars": 30000,
        "doc_scan_patterns": ["**/*.md"],
        "doc_scan_exclude": [".aidlc/**", ".git/**"],
        "run_tests_command": None,
    }


class TestInitRun:
    def test_new_run(self, config):
        state, run_dir = init_run(config, resume=False, dry_run=True)
        assert state.run_id.startswith("aidlc_")
        assert run_dir.exists()
        assert (run_dir / "config_snapshot.json").exists()
        assert (run_dir / "claude_outputs").is_dir()

    def test_resume_no_previous(self, config, capsys):
        state, run_dir = init_run(config, resume=True, dry_run=True)
        captured = capsys.readouterr()
        assert "No previous run" in captured.out or "Starting new run" in captured.out

    def test_dry_run_flag_set(self, config):
        state, run_dir = init_run(config, resume=False, dry_run=True)
        assert config["dry_run"] is True

    @patch("aidlc.runner.os.chmod")
    def test_config_snapshot_restricts_permissions(self, mock_chmod, config):
        _, run_dir = init_run(config, resume=False, dry_run=True)
        assert (run_dir / "config_snapshot.json", 0o600) in [
            call.args for call in mock_chmod.call_args_list
        ]

    @patch("aidlc.runner.os.chmod", side_effect=OSError("chmod not supported"))
    def test_config_snapshot_ignores_chmod_oserror(self, mock_chmod, config):
        state, run_dir = init_run(config, resume=False, dry_run=True)
        assert (run_dir / "config_snapshot.json").exists()


class TestScanProject:
    def test_scans_docs(self, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test Project")
        logger = logging.getLogger("test_scan")
        state = RunState(run_id="t", config_name="c")
        context, scan_result = scan_project(state, config, logger)
        assert "Test Project" in context
        assert state.docs_scanned >= 1

    @patch("aidlc.runner.ProjectScanner")
    def test_logs_when_doc_chars_exceed_threshold(self, MockScanner, config, tmp_path, caplog):
        (tmp_path / "README.md").write_text("# x")
        mock_inst = MagicMock()
        mock_inst.scan.return_value = {
            "total_docs": 2,
            "doc_files": [
                {"path": "a.md", "size": 50000},
                {"path": "b.md", "size": 35000},
            ],
            "project_type": "python",
            "existing_issues": [],
        }
        mock_inst.build_context_prompt.return_value = "context"
        MockScanner.return_value = mock_inst
        logger = logging.getLogger("test_scan_large")
        state = RunState(run_id="t", config_name="c")
        with caplog.at_level(logging.INFO):
            scan_project(state, config, logger)
        assert "Large project" in caplog.text


class TestHydrateExistingIssues:
    def test_hydrates_issue_state_from_scan_result(self):
        state = RunState(run_id="t", config_name="c")
        scan_result = {
            "existing_issues": [
                {
                    "path": ".aidlc/issues/ISSUE-001.md",
                    "content": "",
                    "parsed_issue": {
                        "id": "ISSUE-001",
                        "title": "Existing",
                        "description": "Loaded from disk",
                        "priority": "high",
                        "labels": ["infra"],
                        "dependencies": [],
                        "acceptance_criteria": ["AC1"],
                        "status": "verified",
                        "implementation_notes": "",
                        "verification_result": "",
                        "files_changed": [],
                        "attempt_count": 0,
                        "max_attempts": 3,
                    },
                }
            ]
        }

        hydrate_existing_issues(state, scan_result, logging.getLogger("test"))

        issue = state.get_issue("ISSUE-001")
        assert issue is not None
        assert issue.status == IssueStatus.VERIFIED
        assert state.total_issues == 1

    def test_does_not_downgrade_implemented_when_markdown_still_pending(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            Issue(
                id="ISSUE-001",
                title="From run state",
                description="",
                status=IssueStatus.IMPLEMENTED,
            ).to_dict()
        ]
        scan_result = {
            "existing_issues": [
                {
                    "parsed_issue": {
                        "id": "ISSUE-001",
                        "title": "Stale file",
                        "description": "",
                        "priority": "medium",
                        "labels": [],
                        "dependencies": [],
                        "acceptance_criteria": [],
                        "status": "pending",
                        "implementation_notes": "",
                        "verification_result": "",
                        "files_changed": [],
                        "attempt_count": 0,
                        "max_attempts": 3,
                    },
                }
            ]
        }
        hydrate_existing_issues(state, scan_result, logging.getLogger("test"))
        assert state.get_issue("ISSUE-001").status == IssueStatus.IMPLEMENTED

    def test_skips_entries_without_valid_parsed_issue(self):
        state = RunState(run_id="t", config_name="c")
        scan_result = {
            "existing_issues": [
                {"parsed_issue": None},
                {"parsed_issue": {"title": "no id"}},
            ]
        }
        hydrate_existing_issues(state, scan_result, logging.getLogger("h"))
        assert state.total_issues == 0


class TestRunFull:
    @patch("aidlc.runner.RunLock")
    def test_dry_run_completes(self, MockLock, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test")
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock

        run_full(config=config, dry_run=True, verbose=False)
        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_called()

    @patch("aidlc.runner.RunLock")
    def test_plan_only(self, MockLock, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test")
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock

        run_full(config=config, dry_run=True, plan_only=True, verbose=False)
        mock_lock.release.assert_called()

    @patch("aidlc.runner.sys.exit")
    @patch("aidlc.runner.ProviderRouter")
    @patch("aidlc.runner.RunLock")
    def test_exits_when_no_ai_provider_and_not_dry_run(
        self, MockLock, MockRouter, mock_exit, config, tmp_path
    ):
        (tmp_path / "README.md").write_text("# T")
        cfg = {**config, "dry_run": False}
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock
        mock_cli = MagicMock()
        mock_cli.check_available.return_value = False
        MockRouter.return_value = mock_cli
        mock_exit.side_effect = SystemExit(1)
        with pytest.raises(SystemExit):
            run_full(config=cfg, dry_run=False, verbose=False)
        mock_exit.assert_called_with(1)
        mock_lock.release.assert_called()

    @patch("aidlc.doc_gap_detector.detect_doc_gaps")
    @patch("aidlc.runner.scan_project")
    @patch("aidlc.runner.ProviderRouter")
    @patch("aidlc.runner.RunLock")
    def test_run_full_runs_discovery_then_research_then_planning(
        self,
        MockLock,
        MockRouter,
        mock_scan,
        mock_doc_gaps,
        config,
        tmp_path,
    ):
        """Plan-only run should drive scan → discovery → research → planning
        in order, in line with the new pre-planning phase shape."""
        (tmp_path / "README.md").write_text("# T")
        (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- do thing")
        cfg = {
            **config,
            "dry_run": True,
            "plan_only": True,
            "doc_gap_detection_enabled": True,
        }
        MockLock.return_value = MagicMock()
        mock_cli = MagicMock()
        mock_cli.check_available.return_value = True
        MockRouter.return_value = mock_cli

        def _fake_scan(state, cfg, logger, cli=None):
            state.phase = RunPhase.SCANNING
            state.docs_scanned = 0
            state.scanned_docs = []
            return (
                "ctx",
                {
                    "doc_files": [],
                    "existing_issues": [],
                    "total_docs": 0,
                    "project_type": "py",
                },
            )

        mock_scan.side_effect = _fake_scan
        mock_doc_gaps.return_value = []

        with (
            patch("aidlc.discovery.run_discovery") as MockDiscovery,
            patch("aidlc.research_phase.run_research_phase") as MockResearch,
            patch("aidlc.runner.Planner") as MockPlanner,
        ):
            MockPlanner.return_value.run = MagicMock()
            run_full(config=cfg, dry_run=True, plan_only=True, verbose=False)

        MockDiscovery.assert_called_once()
        MockResearch.assert_called_once()
        mock_cli.set_phase.assert_any_call("planning")

    @patch("aidlc.runner.scan_project", side_effect=KeyboardInterrupt)
    @patch("aidlc.runner.ProviderRouter")
    @patch("aidlc.runner.RunLock")
    def test_keyboard_interrupt_still_releases_lock(
        self, MockLock, MockRouter, _mock_scan, config, tmp_path
    ):
        (tmp_path / "README.md").write_text("# T")
        cfg = {**config, "dry_run": True}
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock
        mock_cli = MagicMock()
        mock_cli.check_available.return_value = True
        MockRouter.return_value = mock_cli
        run_full(config=cfg, dry_run=True, verbose=False)
        mock_lock.release.assert_called()

    @patch("aidlc.runner.scan_project", side_effect=RuntimeError("boom"))
    @patch("aidlc.runner.ProviderRouter")
    @patch("aidlc.runner.RunLock")
    def test_unhandled_exception_marks_failed_and_releases(
        self, MockLock, MockRouter, _mock_scan, config, tmp_path
    ):
        (tmp_path / "README.md").write_text("# T")
        cfg = {**config, "dry_run": True}
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock
        mock_cli = MagicMock()
        mock_cli.check_available.return_value = True
        MockRouter.return_value = mock_cli
        run_full(config=cfg, dry_run=True, verbose=False)
        mock_lock.release.assert_called()
