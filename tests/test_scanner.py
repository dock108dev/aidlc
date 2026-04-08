"""Tests for aidlc.scanner module."""

import pytest
from pathlib import Path

from aidlc.scanner import ProjectScanner, DEFAULT_MAX_DOC_CHARS


@pytest.fixture
def project(tmp_path):
    """Create a minimal project structure for testing."""
    (tmp_path / "README.md").write_text("# My Project\nA test project.")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\nSome guidance.")
    (docs / "api.md").write_text("# API\nAPI docs.")
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    return tmp_path


@pytest.fixture
def config():
    return {
        "doc_scan_patterns": ["**/*.md", "**/*.txt", "**/*.rst"],
        "doc_scan_exclude": [
            "node_modules/**", ".git/**", "venv/**", ".venv/**",
            "__pycache__/**", ".aidlc/**", "dist/**", "build/**",
        ],
        "max_doc_chars": DEFAULT_MAX_DOC_CHARS,
        "max_context_chars": 80000,
    }


class TestProjectScanner:
    def test_scan_finds_docs(self, project, config):
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        assert result["total_docs"] >= 3  # README.md, docs/guide.md, docs/api.md
        paths = [d["path"] for d in result["doc_files"]]
        assert "README.md" in paths

    def test_scan_detects_project_type(self, project, config):
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        assert "python" in result["project_type"]

    def test_scan_respects_exclude(self, project, config):
        # Create a file in an excluded dir
        excluded = project / "node_modules" / "pkg"
        excluded.mkdir(parents=True)
        (excluded / "readme.md").write_text("Excluded")

        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        paths = [d["path"] for d in result["doc_files"]]
        assert not any("node_modules" in p for p in paths)

    def test_doc_priority_root_readme(self, project, config):
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        readme_doc = next(d for d in result["doc_files"] if d["path"] == "README.md")
        assert readme_doc["priority"] == 0

    def test_doc_priority_docs_dir(self, project, config):
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        guide_doc = next(d for d in result["doc_files"] if d["path"] == "docs/guide.md")
        assert guide_doc["priority"] == 1  # docs/ prefix

    def test_doc_truncation(self, project, config):
        config["max_doc_chars"] = 50
        (project / "long.md").write_text("x" * 1000)
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        long_doc = next(d for d in result["doc_files"] if d["path"] == "long.md")
        assert "truncated" in long_doc["content"]
        assert len(long_doc["content"]) < 200

    def test_structure_summary(self, project, config):
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        summary = result["structure_summary"]
        assert "src/" in summary
        assert "docs/" in summary

    def test_existing_issues(self, project, config):
        issues_dir = project / ".aidlc" / "issues"
        issues_dir.mkdir(parents=True)
        (issues_dir / "ISSUE-001.md").write_text("# ISSUE-001\nTest issue")
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        assert len(result["existing_issues"]) == 1

    def test_build_context_prompt(self, project, config):
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        context = scanner.build_context_prompt(result)
        assert "python" in context
        assert "README.md" in context
        assert "Project Structure" in context

    def test_scan_warnings_present(self, project, config):
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        assert "scan_warnings" in result
        assert "skipped_docs" in result["scan_warnings"]

    def test_context_prompt_caps_total_chars(self, project, config):
        config["max_context_chars"] = 100
        # Create many docs
        for i in range(20):
            (project / f"doc_{i:03d}.md").write_text("x" * 50)
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        context = scanner.build_context_prompt(result)
        assert "more docs not shown" in context

    def test_detect_multiple_project_types(self, project, config):
        (project / "package.json").write_text('{"name": "test"}')
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        assert "python" in result["project_type"]
        assert "javascript" in result["project_type"] or "typescript" in result["project_type"]

    def test_unknown_project_type(self, tmp_path, config):
        (tmp_path / "README.md").write_text("# Unknown")
        scanner = ProjectScanner(tmp_path, config)
        result = scanner.scan()
        assert result["project_type"] == "unknown"
