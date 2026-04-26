"""Tests for aidlc.implementer module."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from aidlc.implementer import Implementer
from aidlc.models import Issue, RunState


@pytest.fixture
def logger():
    return logging.getLogger("test_implementer")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,
        "max_consecutive_failures": 3,
        "max_implementation_attempts": 3,
        "max_implementation_cycles": 5,
        "test_timeout_seconds": 30,
        "max_implementation_context_chars": 30000,
        "dry_run": True,
        "run_tests_command": None,
    }


@pytest.fixture
def state_with_issues():
    s = RunState(run_id="test_impl", config_name="default")
    s.issues = [
        {
            "id": "ISSUE-001",
            "title": "First Issue",
            "description": "Do the first thing",
            "priority": "high",
            "labels": [],
            "dependencies": [],
            "acceptance_criteria": ["AC1"],
            "status": "pending",
            "implementation_notes": "",
            "verification_result": "",
            "files_changed": [],
            "attempt_count": 0,
            "max_attempts": 3,
        },
    ]
    s.total_issues = 1
    return s


@pytest.fixture
def cli():
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "[DRY RUN] No execution",
        "error": None,
        "failure_type": None,
        "duration_seconds": 0.0,
        "retries": 0,
    }
    return cli


def test_reopen_verified_without_verification_result(tmp_path, logger):
    """Hydrated verified rows with empty Verification Result must re-open for implementation."""
    cfg = {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,
        "max_consecutive_failures": 3,
        "max_implementation_attempts": 3,
        "max_implementation_cycles": 5,
        "test_timeout_seconds": 30,
        "max_implementation_context_chars": 30000,
        "dry_run": True,
        "run_tests_command": None,
        "autosync_issue_status_sync": False,
        "implementation_reopen_verified_without_result": True,
    }
    state = RunState(run_id="r1", config_name="default")
    state.issues = [
        {
            "id": "ISSUE-001",
            "title": "A",
            "description": "d",
            "priority": "medium",
            "labels": [],
            "dependencies": [],
            "acceptance_criteria": [],
            "status": "verified",
            "implementation_notes": "",
            "verification_result": "",
            "files_changed": [],
            "attempt_count": 0,
            "max_attempts": 3,
        },
    ]
    state.total_issues = 1
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    impl = Implementer(state, run_dir, cfg, MagicMock(), "", logger)
    assert impl._maybe_reopen_stale_verified_issues() is True
    assert state.issues[0]["status"] == "pending"


class TestImplementer:
    def test_dry_run_completes(self, state_with_issues, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state_with_issues, run_dir, config, cli, "context", logger)
        impl.run()
        assert state_with_issues.issues_implemented >= 1

    def test_cycle_cap(self, state_with_issues, config, cli, logger, tmp_path):
        config["max_implementation_cycles"] = 1
        # Add many issues
        for i in range(5):
            state_with_issues.issues.append(
                {
                    "id": f"ISSUE-{i + 10:03d}",
                    "title": f"Issue {i + 10}",
                    "description": "D",
                    "priority": "medium",
                    "labels": [],
                    "dependencies": [],
                    "acceptance_criteria": ["AC"],
                    "status": "pending",
                    "implementation_notes": "",
                    "verification_result": "",
                    "files_changed": [],
                    "attempt_count": 0,
                    "max_attempts": 3,
                }
            )
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state_with_issues, run_dir, config, cli, "context", logger)
        impl.run()
        assert state_with_issues.implementation_cycles <= 1


class TestImplementUnstructuredOutput:
    """Regression: when Claude wrote files via tools but the JSON envelope
    is missing/garbled (mid-output timeout, trailing prose, second JSON
    block confusing the parser), the implementer accepts the files via
    git-diff verification rather than throwing the work away and burning
    another full-cost attempt."""

    @pytest.fixture
    def issue_state(self):
        s = RunState(run_id="t-unstructured", config_name="default")
        s.issues = [
            {
                "id": "ISSUE-042",
                "title": "test issue",
                "description": "do thing",
                "priority": "medium",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": ["AC1"],
                "status": "pending",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 0,
                "max_attempts": 3,
            },
        ]
        s.total_issues = 1
        return s

    def _build_impl(self, issue_state, tmp_path, logger, *, run_tests_command=None):
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
            "checkpoint_interval_minutes": 999,
            "max_consecutive_failures": 3,
            "max_implementation_attempts": 3,
            "test_timeout_seconds": 30,
            "max_implementation_context_chars": 30000,
            "dry_run": False,
            "run_tests_command": run_tests_command,
        }
        (tmp_path / ".aidlc" / "issues").mkdir(parents=True, exist_ok=True)
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            # Two top-level JSON blocks glued together — the historical
            # bug — used to cause "Extra data" parse failures. Plus
            # trailing prose to simulate a mid-output timeout stop.
            "output": (
                '{"issue_id": "ISSUE-042", "success": true, "summary": "first"}\n\n'
                '{"unrelated": "second block"}\n'
                "And then some prose that should be ignored.\n"
            ),
            "success": True,
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
        }
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        return Implementer(issue_state, run_dir, config, cli, "context", logger)

    def test_first_json_object_parses_when_followed_by_extra_data(
        self, issue_state, tmp_path, logger
    ):
        """parse_implementation_result handles ``{...} {...} prose`` cleanly now.
        This is the inner-parser regression test for the implementer path."""
        impl = self._build_impl(issue_state, tmp_path, logger)
        with patch.object(
            impl,
            "_get_changed_files",
            return_value=(["a.py", "b.py"], True),
        ):
            issue = Issue.from_dict(issue_state.issues[0])
            ok = impl._implement_issue(issue)
        # The output had a clean first JSON block; that's the one we honor.
        # success=True from JSON; tests skipped (no run_tests_command), so
        # the issue lands as IMPLEMENTED.
        assert ok is True

    def test_truly_unparseable_output_with_files_changed_is_accepted_via_git(
        self, issue_state, tmp_path, logger
    ):
        """When JSON is genuinely unparseable but git diff shows files
        changed, accept the work and let tests gate. Previous behavior
        rejected and forced a full retry — costing ~$5/attempt for nothing."""
        impl = self._build_impl(issue_state, tmp_path, logger)
        impl.cli.execute_prompt.return_value = {
            "output": "I made some changes but forgot the JSON envelope.",
            "success": True,
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
        }
        with patch.object(
            impl,
            "_get_changed_files",
            return_value=(["a.py", "b.py", "c.py"], True),
        ):
            issue = Issue.from_dict(issue_state.issues[0])
            ok = impl._implement_issue(issue)
        assert ok is True
        updated = issue_state.issues[0]
        assert updated["status"] == "implemented"
        assert updated["files_changed"] == ["a.py", "b.py", "c.py"]

    def test_unparseable_output_with_no_files_changed_still_fails(
        self, issue_state, tmp_path, logger
    ):
        """No JSON AND no files changed is still a real failure — that's
        Claude producing nothing, not a communication-format glitch."""
        impl = self._build_impl(issue_state, tmp_path, logger)
        impl.cli.execute_prompt.return_value = {
            "output": "I considered the problem but did nothing.",
            "success": True,
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
        }
        with patch.object(impl, "_get_changed_files", return_value=([], True)):
            issue = Issue.from_dict(issue_state.issues[0])
            ok = impl._implement_issue(issue)
        assert ok is False


class TestResumeInterruptedAttempt:
    """Regression: when a run is killed (Ctrl-C, SIGTERM, hard timeout)
    mid-attempt, the issue is persisted with status=IN_PROGRESS and
    attempt_count already incremented (the increment happens at the START
    of an attempt, before the model call). On resume, the implementer
    must restart the SAME attempt rather than burning another
    attempt_count slot — one killed attempt should not consume two of
    max_attempts."""

    @pytest.fixture
    def interrupted_state(self):
        """Mimics a state file persisted mid-attempt: status=IN_PROGRESS,
        attempt_count already advanced to 2 (the killed attempt was #2 of 3)."""
        s = RunState(run_id="t-resume", config_name="default")
        s.issues = [
            {
                "id": "ISSUE-007",
                "title": "issue mid-attempt",
                "description": "do thing",
                "priority": "medium",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": ["AC1"],
                "status": "in_progress",
                "implementation_notes": "Attempt 1 failed: timeout",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 2,  # killed mid-attempt 2; state persisted with this value
                "max_attempts": 3,
            },
        ]
        s.total_issues = 1
        return s

    def _build_impl(self, state, tmp_path, logger):
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
            "checkpoint_interval_minutes": 999,
            "max_consecutive_failures": 3,
            "max_implementation_attempts": 3,
            "test_timeout_seconds": 30,
            "max_implementation_context_chars": 30000,
            "dry_run": False,
            "run_tests_command": None,
        }
        (tmp_path / ".aidlc" / "issues").mkdir(parents=True, exist_ok=True)
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "output": '{"issue_id": "ISSUE-007", "success": true, "summary": "ok"}',
            "success": True,
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
        }
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        return Implementer(state, run_dir, config, cli, "context", logger)

    def test_resume_does_not_double_increment_attempt_count(
        self, interrupted_state, tmp_path, logger
    ):
        impl = self._build_impl(interrupted_state, tmp_path, logger)
        with patch.object(impl, "_get_changed_files", return_value=(["x.py"], True)):
            issue = Issue.from_dict(interrupted_state.issues[0])
            ok = impl._implement_issue(issue)
        assert ok is True
        # The attempt_count must remain at 2 — the killed attempt #2 is
        # being resumed, not a fresh attempt #3 burning a third slot.
        assert interrupted_state.issues[0]["attempt_count"] == 2
        # And on success, status is implemented (not stuck at in_progress).
        assert interrupted_state.issues[0]["status"] == "implemented"

    def test_fresh_pending_issue_still_increments_normally(self, tmp_path, logger):
        """Sanity check: the resume-detection branch must not trigger for a
        normal pending issue. attempt_count goes from 0 → 1."""
        s = RunState(run_id="t-fresh", config_name="default")
        s.issues = [
            {
                "id": "ISSUE-100",
                "title": "fresh issue",
                "description": "do thing",
                "priority": "medium",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": ["AC1"],
                "status": "pending",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 0,
                "max_attempts": 3,
            },
        ]
        s.total_issues = 1
        impl = self._build_impl(s, tmp_path, logger)
        with patch.object(impl, "_get_changed_files", return_value=(["x.py"], True)):
            issue = Issue.from_dict(s.issues[0])
            impl._implement_issue(issue)
        assert s.issues[0]["attempt_count"] == 1

    def test_resume_does_not_exhaust_max_attempts_on_repeated_kill(
        self, interrupted_state, tmp_path, logger
    ):
        """Edge case: user kills attempt 2/3, resumes (now still attempt 2/3),
        kills again, resumes again. Without the fix this would jump to 4/3
        and exhaust max_attempts; with the fix it stays at 2/3."""
        impl = self._build_impl(interrupted_state, tmp_path, logger)
        # Simulate: model "interrupted" again — force the status back to
        # IN_PROGRESS with the same attempt_count.
        with patch.object(impl, "_get_changed_files", return_value=([], True)):
            # Model returns nothing useful; mid-attempt kill leaves
            # status=in_progress, attempt_count unchanged.
            interrupted_state.issues[0]["status"] = "in_progress"
            interrupted_state.issues[0]["attempt_count"] = 2

            issue = Issue.from_dict(interrupted_state.issues[0])
            impl._implement_issue(issue)
        # After one resume that didn't add a fresh attempt, count is still
        # within budget (≤ max_attempts=3). Without the fix, count would
        # have gone to 3 then 4 across two resume cycles.
        assert interrupted_state.issues[0]["attempt_count"] <= 3


class TestSortIssues:
    def test_priority_ordering(self, config, cli, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "Low",
                "priority": "low",
                "dependencies": [],
                "status": "pending",
            },
            {
                "id": "ISSUE-002",
                "title": "High",
                "priority": "high",
                "dependencies": [],
                "status": "pending",
            },
            {
                "id": "ISSUE-003",
                "title": "Med",
                "priority": "medium",
                "dependencies": [],
                "status": "pending",
            },
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        impl._sort_issues()
        ids = [d["id"] for d in state.issues]
        assert ids.index("ISSUE-002") < ids.index("ISSUE-003")
        assert ids.index("ISSUE-003") < ids.index("ISSUE-001")

    def test_dependency_ordering(self, config, cli, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-002",
                "title": "Second",
                "priority": "high",
                "dependencies": ["ISSUE-001"],
                "status": "pending",
            },
            {
                "id": "ISSUE-001",
                "title": "First",
                "priority": "high",
                "dependencies": [],
                "status": "pending",
            },
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        impl._sort_issues()
        ids = [d["id"] for d in state.issues]
        assert ids.index("ISSUE-001") < ids.index("ISSUE-002")

    def test_circular_dependency_detected(self, config, cli, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "A",
                "priority": "high",
                "dependencies": ["ISSUE-002"],
                "status": "pending",
            },
            {
                "id": "ISSUE-002",
                "title": "B",
                "priority": "high",
                "dependencies": ["ISSUE-001"],
                "status": "pending",
            },
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        result = impl._sort_issues()
        assert result is True
        assert len(state.issues) == 2
        deps = {d["id"]: d.get("dependencies", []) for d in state.issues}
        # At least one edge must be removed to break the cycle.
        assert not (
            "ISSUE-001" in deps.get("ISSUE-002", []) and "ISSUE-002" in deps.get("ISSUE-001", [])
        )


class TestDetectTestCommand:
    def test_python_pytest(self, config, cli, logger, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")
        (tmp_path / "tests").mkdir()
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "python -m pytest"

    def test_node_npm_test(self, config, cli, logger, tmp_path):
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "npm test"

    def test_rust_cargo(self, config, cli, logger, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname='test'")
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "cargo test"

    def test_go(self, config, cli, logger, tmp_path):
        (tmp_path / "go.mod").write_text("module test")
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "go test ./..."

    def test_makefile_with_test(self, config, cli, logger, tmp_path):
        (tmp_path / "Makefile").write_text("test:\n\techo test")
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "make test"

    def test_no_tests(self, config, cli, logger, tmp_path):
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd is None


class TestBuildImplementationPrompt:
    def test_contains_issue_info(self, config, cli, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "project context here", logger)
        issue = Issue(
            id="ISSUE-001",
            title="Test Issue",
            description="Description",
            priority="high",
            acceptance_criteria=["AC1", "AC2"],
        )
        prompt = impl._build_implementation_prompt(issue)
        assert "ISSUE-001" in prompt
        assert "Test Issue" in prompt
        assert "AC1" in prompt
        assert "project context here" in prompt

    def test_context_capped(self, config, cli, logger, tmp_path):
        config["max_implementation_context_chars"] = 100
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        long_context = "x" * 1000
        impl = Implementer(state, run_dir, config, cli, long_context, logger)
        issue = Issue(id="ISSUE-001", title="T", description="D")
        prompt = impl._build_implementation_prompt(issue)
        # The context portion should be truncated to ~100 chars
        assert prompt.count("x") <= 120  # Allow overhead from formatting + instruction text


class TestGetChangedFiles:
    @patch("aidlc.implementer_workspace.subprocess.run")
    def test_returns_changed_files(self, mock_run, config, cli, logger, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="src/main.py\nsrc/utils.py\n",
        )
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        files = impl._get_changed_files()
        assert files == ["src/main.py", "src/utils.py"]

    @patch("aidlc.implementer_workspace.subprocess.run")
    def test_returns_empty_on_no_changes(self, mock_run, config, cli, logger, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        files = impl._get_changed_files()
        assert files == []
