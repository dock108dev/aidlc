"""Tests for aidlc.precheck module."""

import json
import pytest
from pathlib import Path

from aidlc.precheck import run_precheck, PrecheckResult


@pytest.fixture
def empty_project(tmp_path):
    """A project with no docs at all."""
    return tmp_path


@pytest.fixture
def minimal_project(tmp_path):
    """A project with just ROADMAP.md."""
    (tmp_path / "ROADMAP.md").write_text("# Roadmap\n## Phase 1\n- Build something")
    return tmp_path


@pytest.fixture
def full_project(tmp_path):
    """A project with all docs."""
    (tmp_path / "ROADMAP.md").write_text("# Roadmap")
    (tmp_path / "README.md").write_text("# My Project")
    (tmp_path / "ARCHITECTURE.md").write_text("# Architecture")
    (tmp_path / "DESIGN.md").write_text("# Design")
    (tmp_path / "CLAUDE.md").write_text("# Claude Instructions")
    (tmp_path / "STATUS.md").write_text("# Status")
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "milestones.md").write_text("# Milestones")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "feature.md").write_text("# Feature")
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "patterns.md").write_text("# Patterns")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "api.md").write_text("# API")
    # Add source code
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
    return tmp_path


@pytest.fixture
def python_project(tmp_path):
    """A Python project with source code but minimal docs."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'myapp'")
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    (src / "app.py").write_text("def run(): pass")
    return tmp_path


class TestRunPrecheck:
    def test_empty_project_not_ready(self, empty_project):
        result = run_precheck(empty_project)
        assert not result.ready
        assert "ROADMAP.md" in result.required_missing

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

    def test_minimal_project_is_ready(self, minimal_project):
        result = run_precheck(minimal_project)
        assert result.ready
        assert "ROADMAP.md" in result.required_found

    def test_minimal_project_has_missing_recommended(self, minimal_project):
        result = run_precheck(minimal_project)
        assert "README.md" in result.recommended_missing
        assert "ARCHITECTURE.md" in result.recommended_missing

    def test_full_project_excellent_score(self, full_project):
        result = run_precheck(full_project)
        assert result.ready
        assert result.score == "excellent"

    def test_detects_python_project(self, python_project):
        result = run_precheck(python_project)
        assert "python" in result.project_type

    def test_detects_source_code(self, python_project):
        result = run_precheck(python_project)
        assert result.has_source_code

    def test_no_source_code_in_empty(self, empty_project):
        result = run_precheck(empty_project)
        assert not result.has_source_code

    def test_existing_aidlc_not_recreated(self, minimal_project):
        aidlc_dir = minimal_project / ".aidlc"
        aidlc_dir.mkdir()
        config = {"plan_budget_hours": 2, "custom": True}
        (aidlc_dir / "config.json").write_text(json.dumps(config))

        result = run_precheck(minimal_project)
        assert not result.config_created
        # Custom config should be preserved
        actual = json.loads((aidlc_dir / "config.json").read_text())
        assert actual["custom"] is True
        assert actual["plan_budget_hours"] == 2

    def test_aidlc_dir_without_config_gets_config(self, minimal_project):
        aidlc_dir = minimal_project / ".aidlc"
        aidlc_dir.mkdir()
        # No config.json yet

        result = run_precheck(minimal_project, auto_init=True)
        assert result.config_created
        assert (aidlc_dir / "config.json").exists()


class TestPrecheckResult:
    def test_score_not_ready(self):
        r = PrecheckResult()
        r.required_missing = ["ROADMAP.md"]
        assert r.score == "not ready"

    def test_score_minimal(self):
        r = PrecheckResult()
        r.required_found = ["ROADMAP.md"]
        assert r.score == "minimal"

    def test_score_good(self):
        r = PrecheckResult()
        r.required_found = ["ROADMAP.md"]
        r.recommended_found = ["README.md", "ARCHITECTURE.md", "DESIGN.md"]
        r.optional_found = ["STATUS.md"]
        assert r.score == "good"

    def test_ready_property(self):
        r = PrecheckResult()
        r.required_found = ["ROADMAP.md"]
        assert r.ready

        r2 = PrecheckResult()
        r2.required_missing = ["ROADMAP.md"]
        assert not r2.ready
