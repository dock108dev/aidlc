"""Unit tests for aidlc.implementer_helpers."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from aidlc.implementer_helpers import (
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
    assert fix_failing_tests(impl, issue) is True


def test_fix_failing_tests_cli_fails(mock_impl_module):
    impl, issue = mock_impl_module
    impl._run_tests = MagicMock(return_value="errors")
    impl.cli.execute_prompt.return_value = {"success": False}
    assert fix_failing_tests(impl, issue) is False


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
