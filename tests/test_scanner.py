"""Tests for aidlc.scanner module."""

import pytest
from aidlc.scanner import DEFAULT_MAX_DOC_CHARS, ProjectScanner


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
            "node_modules/**",
            ".git/**",
            "venv/**",
            ".venv/**",
            "__pycache__/**",
            ".aidlc/**",
            "dist/**",
            "build/**",
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

    def test_is_excluded_matches_nested_segment(self, project, config):
        scanner = ProjectScanner(project, config)
        assert scanner._is_excluded("lib/node_modules/some-pkg/readme.md") is True

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

    def test_existing_issue_is_parsed(self, project, config):
        issues_dir = project / ".aidlc" / "issues"
        issues_dir.mkdir(parents=True)
        (issues_dir / "ISSUE-001.md").write_text(
            """# ISSUE-001: Test parsed issue

**Priority**: high
**Labels**: backend, tests
**Dependencies**: ISSUE-000
**Status**: verified

## Description

Implement the thing.

## Acceptance Criteria

- [ ] It works
- [x] It stays working

## Implementation Notes

Done already.
"""
        )
        scanner = ProjectScanner(project, config)
        result = scanner.scan()
        parsed = result["existing_issues"][0]["parsed_issue"]
        assert parsed["id"] == "ISSUE-001"
        assert parsed["title"] == "Test parsed issue"
        assert parsed["priority"] == "high"
        assert parsed["labels"] == ["backend", "tests"]
        assert parsed["dependencies"] == ["ISSUE-000"]
        assert parsed["status"] == "verified"
        assert parsed["acceptance_criteria"] == ["It works", "It stays working"]

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
        assert (
            "javascript" in result["project_type"]
            or "typescript" in result["project_type"]
        )

    def test_unknown_project_type(self, tmp_path, config):
        (tmp_path / "README.md").write_text("# Unknown")
        scanner = ProjectScanner(tmp_path, config)
        result = scanner.scan()
        assert result["project_type"] == "unknown"


class TestDocPhaseClassification:
    """Phase-aware doc classification for planning vs. implementation prompts."""

    def test_planning_only_buckets(self, tmp_path, config):
        scanner = ProjectScanner(tmp_path, config)
        for rel in (
            "BRAINDUMP.md",
            "ROADMAP.md",
            "docs/roadmap.md",
            "AIDLC_FUTURES.md",
            "VISION.md",
            "planning/ideas.md",
            "rfcs/0001-proposal.md",
        ):
            assert scanner._doc_phase(rel) == "planning_only", rel

    def test_implementation_buckets(self, tmp_path, config):
        scanner = ProjectScanner(tmp_path, config)
        for rel in (
            "README.md",
            "ARCHITECTURE.md",
            "DESIGN.md",
            "CLAUDE.md",
            "docs/architecture.md",
            "docs/testing.md",
            "docs/setup.md",
            "docs/contributing.md",
            "docs/configuration-deployment.md",
            "specs/api.md",
        ):
            assert scanner._doc_phase(rel) == "implementation", rel

    def test_unmatched_falls_through_to_both(self, tmp_path, config):
        scanner = ProjectScanner(tmp_path, config)
        for rel in ("docs/content-data.md", "docs/index.md", "notes/random.md"):
            assert scanner._doc_phase(rel) == "both", rel

    def test_empty_planning_only_disables_filter(self, tmp_path, config):
        config = {
            **config,
            "implementation_doc_phase_patterns": {
                "planning_only": [],
                "implementation": [],
            },
        }
        scanner = ProjectScanner(tmp_path, config)
        assert scanner._doc_phase("BRAINDUMP.md") == "both"

    def test_impl_context_drops_planning_only_docs(self, tmp_path, config):
        (tmp_path / "README.md").write_text("# readme body")
        (tmp_path / "BRAINDUMP.md").write_text("# braindump body")
        (tmp_path / "ROADMAP.md").write_text("# roadmap body")
        scanner = ProjectScanner(tmp_path, config)
        result = scanner.scan()

        planning = scanner.build_context_prompt(result, mode="planning")
        impl = scanner.build_context_prompt(result, mode="implementation")

        assert "braindump body" in planning
        assert "roadmap body" in planning
        assert "braindump body" not in impl
        assert "roadmap body" not in impl
        assert "readme body" in impl
        assert "omitted 2 planning-phase doc" in impl

    def test_impl_context_skips_existing_issues_block(self, tmp_path, config):
        (tmp_path / "README.md").write_text("# readme")
        issues_dir = tmp_path / ".aidlc" / "issues"
        issues_dir.mkdir(parents=True)
        (issues_dir / "ISSUE-001.md").write_text(
            "# ISSUE-001: foo\n\n**Priority**: high\n**Status**: pending\n"
        )
        scanner = ProjectScanner(tmp_path, config)
        result = scanner.scan()

        planning = scanner.build_context_prompt(result, mode="planning")
        impl = scanner.build_context_prompt(result, mode="implementation")

        assert "Existing Issues" in planning
        assert "Existing Issues" not in impl

    def test_impl_context_uses_tighter_per_doc_cap(self, tmp_path, config):
        config = {**config, "implementation_max_doc_chars": 100}
        (tmp_path / "README.md").write_text("x" * 3000)
        scanner = ProjectScanner(tmp_path, config)
        result = scanner.scan()

        impl = scanner.build_context_prompt(result, mode="implementation")
        # README appears once; its body is capped at 100 chars + truncation note
        assert "truncated for impl context" in impl
        # Planning keeps the longer (max_doc_chars-capped) body
        planning = scanner.build_context_prompt(result, mode="planning")
        assert "truncated for impl context" not in planning
