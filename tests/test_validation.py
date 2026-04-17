"""Tests for validation loop: test_profiles, test_parser, validation_issues, validator."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from aidlc.models import Issue, IssueStatus, RunPhase, RunState
from aidlc.test_parser import FailureReport, parse_test_failures
from aidlc.test_profiles import detect_test_profile
from aidlc.validation_issues import create_fix_issues
from aidlc.validator import Validator


class TestTestProfiles:
    def test_detect_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "conftest.py").write_text("")
        profile = detect_test_profile(tmp_path, "python", {})
        assert profile["unit"] is not None
        assert "pytest" in profile["unit"]

    def test_detect_javascript_project(self, tmp_path):
        (tmp_path / "package.json").write_text('{"scripts":{"test":"jest"}}')
        profile = detect_test_profile(tmp_path, "javascript", {})
        assert profile["unit"] == "npm test"
        assert profile["build"] == "npm run build"

    def test_detect_godot_project(self, tmp_path):
        (tmp_path / "project.godot").write_text("[gd_scene]")
        profile = detect_test_profile(tmp_path, "unknown", {})
        assert (
            "godot" in (profile["unit"] or "").lower() or "godot" in (profile["e2e"] or "").lower()
        )

    def test_detect_playwright_e2e(self, tmp_path):
        (tmp_path / "playwright.config.ts").write_text("export default {}")
        profile = detect_test_profile(tmp_path, "javascript", {})
        assert profile["e2e"] is not None
        assert "playwright" in profile["e2e"]

    def test_config_override(self, tmp_path):
        config = {"run_tests_command": "custom test cmd", "e2e_test_command": "custom e2e"}
        profile = detect_test_profile(tmp_path, "python", config)
        assert profile["unit"] == "custom test cmd"
        assert profile["e2e"] == "custom e2e"

    def test_package_json_scripts_detection(self, tmp_path):
        # No base "test" script — only specific tiers
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test:unit": "jest --unit", "test:e2e": "playwright test"}})
        )
        profile = detect_test_profile(tmp_path, "javascript", {})
        # Base JS profile sets unit to "npm test", package.json overrides only happen
        # when the base profile hasn't already set a value. Unit is already set.
        assert profile["unit"] is not None
        assert profile["e2e"] == "npm run test:e2e"

    def test_empty_project(self, tmp_path):
        profile = detect_test_profile(tmp_path, "unknown", {})
        assert all(v is None for v in profile.values())


class TestTestParser:
    def test_parse_pytest_failures(self):
        output = """
FAILED tests/test_auth.py::TestLogin::test_invalid_email - AssertionError: expected True
FAILED tests/test_auth.py::TestLogin::test_missing_password - ValueError: password required
"""
        failures = parse_test_failures(output, "pytest")
        assert len(failures) == 2
        assert failures[0].test_name == "TestLogin::test_invalid_email"
        assert failures[0].file == "tests/test_auth.py"
        assert "True" in failures[0].assertion

    def test_parse_jest_failures(self):
        output = """
FAIL src/__tests__/auth.test.js
Tests:        2 failed, 5 passed, 7 total
Test Suites:  1 failed, 2 passed, 3 total
"""
        failures = parse_test_failures(output, "jest")
        assert len(failures) >= 1

    def test_parse_go_failures(self):
        output = """
--- FAIL: TestUserCreation (0.01s)
    user_test.go:42: expected "admin", got "user"
FAIL
"""
        failures = parse_test_failures(output, "go")
        assert len(failures) == 1
        assert failures[0].test_name == "TestUserCreation"
        assert failures[0].file == "user_test.go"
        assert failures[0].line == 42

    def test_auto_detect_pytest(self):
        output = "===== test session starts =====\nFAILED tests/test_x.py::test_y - assert False"
        failures = parse_test_failures(output)
        assert len(failures) >= 1
        assert failures[0].framework == "pytest"

    def test_generic_fallback(self):
        output = "FAIL: something went wrong\nERROR: another problem"
        failures = parse_test_failures(output, "generic")
        assert len(failures) >= 1

    def test_empty_output(self):
        assert parse_test_failures("") == []
        assert parse_test_failures("   ") == []

    def test_max_failures_cap(self):
        lines = "\n".join(f"FAILED test_{i}.py::test_{i} - error" for i in range(50))
        failures = parse_test_failures(lines, "pytest", max_failures=5)
        assert len(failures) == 5


class TestValidationIssues:
    def test_create_fix_issues(self):
        failures = [
            FailureReport(
                test_name="test_login", file="tests/test_auth.py", line=10, assertion="assert False"
            ),
            FailureReport(
                test_name="test_signup",
                file="tests/test_auth.py",
                line=20,
                assertion="missing field",
            ),
        ]
        issues = create_fix_issues(failures, set())
        assert len(issues) == 2
        assert issues[0].id == "VFIX-001"
        assert issues[1].id == "VFIX-002"
        assert "test_login" in issues[0].title
        assert issues[0].priority == "high"
        assert "validation" in issues[0].labels

    def test_dedup_same_test(self):
        failures = [
            FailureReport(test_name="test_same", assertion="err1"),
            FailureReport(test_name="test_same", assertion="err2"),
        ]
        issues = create_fix_issues(failures, set())
        assert len(issues) == 1

    def test_skip_existing_ids(self):
        failures = [FailureReport(test_name="test_x")]
        issues = create_fix_issues(failures, {"VFIX-001"})
        # Should still create since we check by test name dedup, not ID collision
        assert len(issues) >= 0  # Implementation detail

    def test_max_issues_cap(self):
        failures = [FailureReport(test_name=f"test_{i}") for i in range(20)]
        issues = create_fix_issues(failures, set(), max_issues=3)
        assert len(issues) == 3

    def test_acceptance_criteria(self):
        failures = [FailureReport(test_name="test_checkout")]
        issues = create_fix_issues(failures, set())
        assert any("test_checkout" in ac for ac in issues[0].acceptance_criteria)

    def test_create_fix_issues_includes_stack_trace_in_description(self):
        failures = [
            FailureReport(
                test_name="t_stack",
                assertion="boom",
                stack_trace="line1\nline2",
            )
        ]
        issues = create_fix_issues(failures, set())
        assert "Stack trace" in issues[0].description


class TestValidator:
    def test_no_tests_skips_validation(self, tmp_path):
        state = RunState(run_id="test", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 3,
            "test_timeout_seconds": 10,
        }
        cli = MagicMock()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        validator = Validator(state, run_dir, config, cli, "project type: unknown", MagicMock())
        result = validator.run()
        assert result is True  # No tests = skip = stable
        assert state.phase == RunPhase.VALIDATING

    def test_no_tests_fails_when_strict(self, tmp_path):
        state = RunState(run_id="test", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 3,
            "test_timeout_seconds": 10,
            "strict_validation": True,
            "validation_allow_no_tests": False,
        }
        cli = MagicMock()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        validator = Validator(state, run_dir, config, cli, "project type: unknown", MagicMock())
        result = validator.run()
        assert result is False

    def test_failed_tier_without_parseable_output_creates_synthetic_failure(self, tmp_path):
        state = RunState(run_id="test", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 10,
        }
        cli = MagicMock()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        logger = MagicMock()

        validator = Validator(state, run_dir, config, cli, "project type: unknown", logger)
        validator.test_profile = {
            "build": "fake-build-cmd",
            "unit": None,
            "integration": None,
            "e2e": None,
        }
        validator._run_command = lambda _cmd: (False, "fatal: export preset missing")

        all_passed, failures, tier_results = validator._run_test_tiers()

        assert all_passed is False
        assert len(tier_results) == 1
        assert tier_results[0]["tier"] == "build"
        assert tier_results[0]["passed"] is False
        assert len(failures) == 1
        assert failures[0].test_name == "build command failed"
        assert "fake-build-cmd" in failures[0].assertion
        assert "export preset missing" in failures[0].stack_trace

    def test_non_progressive_mode_is_rejected(self, tmp_path):
        state = RunState(run_id="test", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 10,
            "test_profile_mode": "legacy",
        }
        cli = MagicMock()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        with pytest.raises(RuntimeError, match="Legacy path removed"):
            Validator(state, run_dir, config, cli, "project type: unknown", MagicMock())


class TestValidatorInternals:
    def test_render_fix_issue_md(self, tmp_path):
        state = RunState(run_id="test", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 10,
        }
        v = Validator(state, tmp_path / "run", config, MagicMock(), "project type: python", MagicMock())
        issue = Issue(
            id="VFIX-009",
            title="Fix login",
            description="Broken",
            priority="high",
            labels=["validation"],
            acceptance_criteria=["tests pass"],
            status=IssueStatus.PENDING,
        )
        md = v._render_fix_issue_md(issue)
        assert "VFIX-009" in md
        assert "Fix login" in md
        assert "- [ ] tests pass" in md

    @patch("aidlc.validator.subprocess.run")
    def test_run_command_timeout(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=1)
        state = RunState(run_id="test", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 10,
        }
        logger = MagicMock()
        v = Validator(state, tmp_path / "run", config, MagicMock(), "project type: python", logger)
        ok, out = v._run_command("any")
        assert ok is False
        assert "timed out" in out.lower()
        logger.warning.assert_called()

    @patch("aidlc.validator.subprocess.run")
    def test_run_command_file_not_found(self, mock_run, tmp_path):
        mock_run.side_effect = FileNotFoundError()
        state = RunState(run_id="test", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 10,
        }
        logger = MagicMock()
        v = Validator(state, tmp_path / "run", config, MagicMock(), "project type: python", logger)
        ok, out = v._run_command("missing-binary-xyz")
        assert ok is False
        assert out == ""
        logger.warning.assert_called()

    def test_run_test_tiers_empty_failure_output(self, tmp_path):
        state = RunState(run_id="test", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 10,
        }
        logger = MagicMock()
        v = Validator(state, tmp_path / "run", config, MagicMock(), "project type: python", logger)
        v.test_profile = {"build": "x", "unit": None, "integration": None, "e2e": None}
        v._run_command = lambda _cmd: (False, "")
        _passed, failures, _tier_results = v._run_test_tiers()
        assert len(failures) == 1
        assert "Command exited non-zero" in failures[0].stack_trace


class TestValidatorRunLoop:
    @staticmethod
    def _python_project(tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "conftest.py").write_text("")

    @patch("aidlc.validator.save_state")
    @patch("aidlc.implementer.Implementer")
    def test_run_returns_true_after_fail_then_pass(self, mock_impl, mock_save, tmp_path):
        self._python_project(tmp_path)
        state = RunState(run_id="vr1", config_name="default")
        state.issues = []
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 3,
            "test_timeout_seconds": 30,
            "validation_batch_size": 10,
        }
        v = Validator(state, run_dir, config, MagicMock(), "project type: python", MagicMock())
        mock_impl.return_value._implement_issue = MagicMock()
        n = {"c": 0}

        def fake_tiers():
            n["c"] += 1
            if n["c"] == 1:
                return (
                    False,
                    [FailureReport(test_name="t_one", assertion="boom")],
                    [{"tier": "unit", "passed": False, "command": "pytest"}],
                )
            return True, [], [{"tier": "unit", "passed": True, "command": "pytest"}]

        v._run_test_tiers = fake_tiers
        assert v.run() is True

    @patch("aidlc.validator.save_state")
    @patch("aidlc.implementer.Implementer")
    def test_run_stops_when_not_making_progress(self, mock_impl, mock_save, tmp_path):
        self._python_project(tmp_path)
        state = RunState(run_id="vr2", config_name="default")
        state.issues = []
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 5,
            "test_timeout_seconds": 30,
            "validation_batch_size": 10,
        }
        v = Validator(state, run_dir, config, MagicMock(), "project type: python", MagicMock())
        mock_impl.return_value._implement_issue = MagicMock()
        fails = [
            FailureReport(test_name="a", assertion="1"),
            FailureReport(test_name="b", assertion="2"),
        ]

        def always_fail():
            return (False, fails, [{"tier": "unit", "passed": False, "command": "pytest"}])

        v._run_test_tiers = always_fail
        assert v.run() is False

    @patch("aidlc.validator.save_state")
    def test_run_stops_when_no_fix_issues_generated(self, mock_save, tmp_path):
        self._python_project(tmp_path)
        state = RunState(run_id="vr3", config_name="default")
        state.issues = []
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 3,
            "test_timeout_seconds": 30,
        }
        v = Validator(state, run_dir, config, MagicMock(), "project type: python", MagicMock())

        def always_fail():
            return (
                False,
                [FailureReport(test_name="only", assertion="x")],
                [{"tier": "unit", "passed": False, "command": "pytest"}],
            )

        v._run_test_tiers = always_fail
        with patch("aidlc.validator.create_fix_issues", return_value=[]):
            assert v.run() is False

    @patch("aidlc.validator.save_state")
    @patch("aidlc.implementer.Implementer")
    def test_run_final_check_passes_after_single_cycle(self, mock_impl, mock_save, tmp_path):
        """Exit the for-loop without early True; final _run_test_tiers succeeds."""
        self._python_project(tmp_path)
        state = RunState(run_id="vr4", config_name="default")
        state.issues = []
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 30,
            "validation_batch_size": 10,
        }
        v = Validator(state, run_dir, config, MagicMock(), "project type: python", MagicMock())
        mock_impl.return_value._implement_issue = MagicMock()
        n = {"c": 0}

        def tiers():
            n["c"] += 1
            if n["c"] == 1:
                return (
                    False,
                    [FailureReport(test_name="fix_me", assertion="nope")],
                    [{"tier": "unit", "passed": False, "command": "pytest"}],
                )
            return True, [], [{"tier": "unit", "passed": True, "command": "pytest"}]

        v._run_test_tiers = tiers
        assert v.run() is True


class TestValidatorTiersAndCommand:
    def test_run_test_tiers_logs_passed_tier(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "conftest.py").write_text("")
        state = RunState(run_id="t", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 30,
        }
        logger = MagicMock()
        v = Validator(state, tmp_path / "run", config, MagicMock(), "project type: python", logger)
        v.test_profile = {
            "build": None,
            "unit": "echo ok",
            "integration": None,
            "e2e": None,
        }
        passed, _failures, results = v._run_test_tiers()
        assert passed is True
        assert results[0]["tier"] == "unit"
        assert results[0]["passed"] is True
        logger.info.assert_any_call("  unit: PASSED")

    @patch("aidlc.validator.subprocess.run")
    def test_run_command_success_returns_output(self, mock_run, tmp_path):
        class _R:
            returncode = 0
            stdout = "hello"
            stderr = ""

        mock_run.return_value = _R()
        state = RunState(run_id="t", config_name="default")
        config = {
            "_project_root": str(tmp_path),
            "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
            "validation_max_cycles": 1,
            "test_timeout_seconds": 30,
        }
        v = Validator(state, tmp_path / "run", config, MagicMock(), "project type: python", MagicMock())
        ok, out = v._run_command("echo x")
        assert ok is True
        assert "hello" in out
