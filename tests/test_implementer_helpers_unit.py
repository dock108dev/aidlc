"""Unit tests for aidlc.implementer_helpers."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from aidlc.implementer_helpers import (
    _looks_like_pre_existing_unrelated_debt,
    _resolve_follow_up_documentation,
    build_implementation_prompt,
    detect_test_command,
    ensure_test_deps,
    fix_failing_tests,
    implementation_instructions,
)
from aidlc.models import Issue, IssueStatus, RunState


def test_implementation_instructions_includes_test_line():
    text = implementation_instructions("pytest -q")
    assert "pytest -q" in text


def test_build_implementation_prompt_truncates_context(tmp_path):
    cfg = {
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "implementation_completed_issues_max": 2,
    }
    (tmp_path / ".aidlc" / "issues").mkdir(parents=True)
    (tmp_path / ".aidlc" / "issues" / "ISSUE-1.md").write_text("from file")
    impl = MagicMock()
    impl.config = cfg
    impl.test_command = None
    impl.max_impl_context_chars = 200
    impl.project_context = "A" * 500
    impl.state = RunState(run_id="r", config_name="c")
    impl.state.issues = [
        {
            "id": "ISSUE-0",
            "title": "done",
            "status": "implemented",
        },
        {
            "id": "ISSUE-9",
            "title": "done2",
            "status": "verified",
        },
    ]
    issue = Issue(
        id="ISSUE-1",
        title="Do work",
        description="D",
        priority="high",
        acceptance_criteria=["c1"],
        attempt_count=1,
    )
    prompt = build_implementation_prompt(impl, issue)
    assert "context truncated" in prompt
    assert "ISSUE-1.md" in prompt


def test_build_implementation_prompt_without_issue_file_uses_description(tmp_path):
    cfg = {
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "implementation_completed_issues_max": 5,
    }
    (tmp_path / ".aidlc" / "issues").mkdir(parents=True)
    impl = MagicMock()
    impl.config = cfg
    impl.test_command = None
    impl.max_impl_context_chars = 12000
    impl.project_context = "ctx"
    impl.state = RunState(run_id="r", config_name="c")
    impl.state.issues = []
    issue = Issue(
        id="ISSUE-99",
        title="T",
        description="Body here",
        priority="low",
        labels=["a", "b"],
        dependencies=["ISSUE-1"],
        acceptance_criteria=["ac"],
        attempt_count=2,
        implementation_notes="prev fail",
    )
    prompt = build_implementation_prompt(impl, issue)
    assert "Body here" in prompt
    assert "Previous attempt notes" in prompt


@pytest.mark.parametrize(
    "layout,expected_substr",
    [
        (("pyproject.toml", "pytest.ini"), "python -m pytest"),
        (("pyproject.toml", "tests"), "python -m pytest"),
        (("package.json",), "npm test"),
        (("Cargo.toml",), "cargo test"),
        (("go.mod",), "go test"),
    ],
)
def test_detect_test_command_variants(tmp_path, layout, expected_substr):
    for name in layout:
        p = tmp_path / name
        if name == "tests":
            p.mkdir()
        elif name == "pytest.ini":
            p.write_text("[pytest]\n")
        elif name == "package.json":
            p.write_text(json.dumps({"scripts": {"test": "jest"}}))
        else:
            p.write_text("")
    cmd = detect_test_command(tmp_path)
    assert expected_substr in (cmd or "")


def test_detect_test_command_makefile(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\ntest:\n\techo ok\n")
    assert detect_test_command(tmp_path) == "make test"


def test_detect_test_command_makefile_read_oserror(tmp_path):
    m = tmp_path / "Makefile"
    m.write_text("x")

    real_read_text = type(m).read_text

    def boom(self, *a, **kw):
        if self == m:
            raise OSError("e")
        return real_read_text(self, *a, **kw)

    with patch.object(type(m), "read_text", boom):
        assert detect_test_command(tmp_path) is None


@patch("aidlc.implementer_helpers.subprocess.run")
def test_ensure_test_deps_runs_go_mod_when_present(mock_run, tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    logger = logging.getLogger("t")
    ensure_test_deps(tmp_path, "go test ./...", logger, state=None)
    assert mock_run.called


@patch("aidlc.implementer_helpers.subprocess.run")
def test_ensure_test_deps_pytest_tool_install_branch(mock_run, tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    mock_run.return_value = MagicMock(returncode=1)
    logger = MagicMock()
    state = RunState(run_id="r", config_name="c")
    ensure_test_deps(tmp_path, "pytest -q", logger, state=state)
    assert state.console_seconds >= 0


def test_fix_failing_tests_success_reruns(mock_impl_module):
    impl, issue = mock_impl_module
    impl._run_tests = MagicMock(side_effect=["fail out", True])
    impl.cli.execute_prompt.return_value = {
        "success": True,
        "output": "{}",
        "duration_seconds": 0.5,
    }
    out = fix_failing_tests(impl, issue)
    assert out.tests_now_passing is True


def test_fix_failing_tests_forwards_files_changed_to_run_tests(mock_impl_module):
    impl, issue = mock_impl_module
    impl._run_tests = MagicMock(side_effect=["out", True])
    impl.cli.execute_prompt.return_value = {
        "success": True,
        "output": "{}",
        "duration_seconds": 0.1,
    }
    fc = ["res://tests/gut/test_x.gd"]
    out = fix_failing_tests(impl, issue, files_changed=fc)
    assert out.tests_now_passing is True
    assert impl._run_tests.call_count == 2
    assert impl._run_tests.call_args_list[0].kwargs.get("files_changed") == fc
    assert impl._run_tests.call_args_list[1].kwargs.get("files_changed") == fc


def test_fix_failing_tests_cli_fails(mock_impl_module):
    impl, issue = mock_impl_module
    impl._run_tests = MagicMock(return_value="errors")
    impl.cli.execute_prompt.return_value = {"success": False}
    out = fix_failing_tests(impl, issue)
    assert out.tests_now_passing is False


def test_looks_like_pre_existing_user_log_sample():
    msg = (
        "Focused GUT coverage passes for `test_x.gd`, but the required broader GUT gate is "
        "blocked by pre-existing unrelated suite issues: parse errors, unrelated failing tests."
    )
    assert _looks_like_pre_existing_unrelated_debt(msg) is True


def test_resolve_follow_up_prose_when_no_json():
    doc = _resolve_follow_up_documentation(
        None,
        "Focused coverage passes but gate is blocked by pre-existing unrelated suite issues.",
        "",
        40,
        True,
    )
    assert "pre-existing" in doc.lower()


def test_fix_failing_tests_accept_pre_existing_debt(mock_impl_module):
    impl, issue = mock_impl_module
    impl.config = {
        "implementation_accept_pre_existing_suite_failures": True,
        "implementation_pre_existing_debt_min_chars": 20,
    }
    impl._run_tests = MagicMock(side_effect=["fail", False])
    impl.cli.execute_prompt.return_value = {
        "success": True,
        "output": """```json
{
  "tests_now_passing": false,
  "failures_are_pre_existing_unrelated": true,
  "follow_up_documentation": "Unrelated parse errors in other_test.gd block the gate."
}
```""",
        "duration_seconds": 0.5,
    }
    out = fix_failing_tests(impl, issue)
    assert out.tests_now_passing is False
    assert out.accepted_pre_existing_debt is True
    assert "unrelated" in out.follow_up_documentation.lower()


def test_fix_failing_tests_prose_only_no_json(mock_impl_module):
    impl, issue = mock_impl_module
    impl.config = {
        "implementation_accept_pre_existing_suite_failures": True,
        "implementation_pre_existing_debt_min_chars": 40,
        "implementation_pre_existing_prose_heuristic": True,
    }
    prose = (
        "Focused GUT coverage passes for res://tests/gut/test_foo.gd, but the broader GUT gate "
        "is blocked by pre-existing unrelated suite issues: parse errors in other files."
    )
    impl._run_tests = MagicMock(side_effect=["fail", False])
    impl.cli.execute_prompt.return_value = {
        "success": True,
        "output": prose,
        "duration_seconds": 0.5,
    }
    out = fix_failing_tests(impl, issue)
    assert out.accepted_pre_existing_debt is True
    assert "pre-existing" in out.follow_up_documentation.lower()


@pytest.fixture
def mock_impl_module():
    impl = MagicMock()
    impl.project_root = MagicMock()
    impl.logger = logging.getLogger("fix")
    impl.config = {}
    impl.state = RunState(run_id="r", config_name="c")
    impl.cli = MagicMock()
    issue = Issue(
        id="I-1",
        title="T",
        description="d",
        acceptance_criteria=["a1"],
        status=IssueStatus.FAILED,
    )
    return impl, issue
