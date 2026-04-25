"""Tests for aidlc.precheck module.

The precheck contract collapsed: BRAINDUMP.md is the only required doc.
README/ARCHITECTURE/DESIGN/CLAUDE/ROADMAP/STATUS and the planning/specs/
design/docs subdirs are no longer tracked or scored — the planner reads
the repo on demand and BRAINDUMP carries cycle intent.
"""

import json

import pytest
from aidlc.precheck import PrecheckResult, run_precheck


@pytest.fixture
def empty_project(tmp_path):
    """A project with no docs at all."""
    return tmp_path


@pytest.fixture
def project_with_braindump(tmp_path):
    """A project with the required BRAINDUMP.md present."""
    (tmp_path / "BRAINDUMP.md").write_text("# Cycle intent\nFix X.")
    return tmp_path


@pytest.fixture
def python_project(tmp_path):
    """A Python project with source code but no BRAINDUMP."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'myapp'")
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    (src / "app.py").write_text("def run(): pass")
    return tmp_path


class TestRunPrecheck:
    def test_empty_project_is_not_ready(self, empty_project):
        # No BRAINDUMP.md → not ready. The whole point of the new contract.
        result = run_precheck(empty_project)
        assert not result.ready
        assert "BRAINDUMP.md" in result.required_missing

    def test_project_with_braindump_is_ready(self, project_with_braindump):
        result = run_precheck(project_with_braindump)
        assert result.ready
        assert "BRAINDUMP.md" in result.required_found
        assert result.required_missing == []

    def test_empty_project_auto_creates_aidlc(self, empty_project):
        result = run_precheck(empty_project, auto_init=True)
        assert result.config_created
        assert (empty_project / ".aidlc" / "config.json").exists()
        config = json.loads((empty_project / ".aidlc" / "config.json").read_text())
        assert "plan_budget_hours" in config

    def test_empty_project_creates_gitignore(self, empty_project):
        run_precheck(empty_project, auto_init=True)
        gitignore = empty_project / ".gitignore"
        assert gitignore.exists()
        assert ".aidlc/runs/" in gitignore.read_text()

    def test_no_auto_init_when_disabled(self, empty_project):
        result = run_precheck(empty_project, auto_init=False)
        assert not result.config_created
        assert not (empty_project / ".aidlc").exists()

    def test_detects_python_project(self, python_project):
        result = run_precheck(python_project)
        assert "python" in result.project_type

    def test_detects_source_code(self, python_project):
        result = run_precheck(python_project)
        assert result.has_source_code

    def test_no_source_code_in_empty(self, empty_project):
        result = run_precheck(empty_project)
        assert not result.has_source_code

    def test_existing_aidlc_not_recreated(self, project_with_braindump):
        aidlc_dir = project_with_braindump / ".aidlc"
        aidlc_dir.mkdir()
        config = {"plan_budget_hours": 2, "custom": True}
        (aidlc_dir / "config.json").write_text(json.dumps(config))

        result = run_precheck(project_with_braindump)
        assert not result.config_created
        actual = json.loads((aidlc_dir / "config.json").read_text())
        assert actual["custom"] is True
        assert actual["plan_budget_hours"] == 2

    def test_aidlc_dir_without_config_gets_config(self, project_with_braindump):
        aidlc_dir = project_with_braindump / ".aidlc"
        aidlc_dir.mkdir()

        result = run_precheck(project_with_braindump, auto_init=True)
        assert result.config_created
        assert (aidlc_dir / "config.json").exists()


class TestPrecheckResult:
    def test_ready_property_reflects_required_missing(self):
        r = PrecheckResult()
        # Default: nothing missing yet → ready (run_precheck has not populated it).
        assert r.ready
        r.required_missing = ["BRAINDUMP.md"]
        assert not r.ready
        r.required_missing = []
        r.required_found = ["BRAINDUMP.md"]
        assert r.ready
