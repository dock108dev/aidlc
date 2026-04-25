"""Tests for aidlc.doc_gap_detector module."""

from pathlib import Path
from unittest.mock import patch

import pytest
from aidlc.audit_models import DocGap
from aidlc.doc_gap_detector import (
    _is_excluded,
    _is_likely_false_positive,
    detect_doc_gaps,
)


@pytest.fixture
def config():
    return {
        "doc_scan_patterns": ["**/*.md"],
        "doc_scan_exclude": ["node_modules/**", ".git/**", ".aidlc/**"],
        "doc_gap_max_items": 50,
    }


class TestDetectDocGaps:
    def test_detects_tbd(self, tmp_path, config):
        (tmp_path / "ROADMAP.md").write_text(
            "# Roadmap\n\n## Phase 1\nScoring algorithm: TBD\n"
        )
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) >= 1
        assert any(g.pattern.upper() == "TBD" for g in gaps)

    def test_detects_design_tbd_as_critical(self, tmp_path, config):
        (tmp_path / "DESIGN.md").write_text(
            "# Design\n\nCaching strategy: design TBD\n"
        )
        gaps = detect_doc_gaps(tmp_path, config)
        critical = [g for g in gaps if g.severity == "critical"]
        assert len(critical) >= 1

    def test_detects_needs_research(self, tmp_path, config):
        (tmp_path / "ROADMAP.md").write_text(
            "# Plan\n\nFormula needs research before implementation\n"
        )
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) >= 1
        assert any("research" in g.pattern.lower() for g in gaps)

    def test_detects_formula_needed(self, tmp_path, config):
        (tmp_path / "DESIGN.md").write_text(
            "# Design\n\nEdge weight calculation: formula needed\n"
        )
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) >= 1
        critical = [g for g in gaps if g.severity == "critical"]
        assert len(critical) >= 1

    def test_detects_placeholder_braces(self, tmp_path, config):
        (tmp_path / "README.md").write_text(
            "# {Project Name}\n\nBuilt by {author name}\n"
        )
        gaps = detect_doc_gaps(tmp_path, config)
        info_gaps = [g for g in gaps if g.severity == "info"]
        assert len(info_gaps) >= 1

    def test_skips_code_backtick_placeholders(self, tmp_path, config):
        (tmp_path / "README.md").write_text("Use `{config_key}` to set the value\n")
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) == 0

    def test_skips_json_like_braces(self, tmp_path, config):
        (tmp_path / "docs.md").write_text('Example: {"key": "value"}\n')
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) == 0

    def test_skips_template_syntax(self, tmp_path, config):
        (tmp_path / "docs.md").write_text("Template: {{variable}} and ${env_var}\n")
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) == 0

    def test_excludes_node_modules(self, tmp_path, config):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "README.md").write_text("TBD everywhere\n")
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) == 0

    def test_excludes_aidlc_dir(self, tmp_path, config):
        aidlc = tmp_path / ".aidlc"
        aidlc.mkdir()
        (aidlc / "notes.md").write_text("TBD\n")
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) == 0

    def test_empty_project_returns_empty(self, tmp_path, config):
        gaps = detect_doc_gaps(tmp_path, config)
        assert gaps == []

    def test_skips_doc_when_read_text_raises(self, tmp_path, config, caplog):
        bad = tmp_path / "bad.md"
        bad.write_text("ignored")
        (tmp_path / "good.md").write_text("Still TBD here\n")
        real_read = Path.read_text

        def selective(self, *a, **kw):
            if self.resolve() == bad.resolve():
                raise OSError("unreadable")
            return real_read(self, *a, **kw)

        caplog.set_level("WARNING")
        with patch.object(Path, "read_text", selective):
            gaps = detect_doc_gaps(tmp_path, config)
        assert any(g.doc_path == "good.md" for g in gaps)
        assert "skipped" in caplog.text.lower()

    def test_skips_backtick_wrapped_placeholder_info(self, tmp_path, config):
        (tmp_path / "cfg.md").write_text("Set `{foo}` as the token.\n")
        gaps = detect_doc_gaps(tmp_path, config)
        assert gaps == []

    def test_no_gaps_in_clean_docs(self, tmp_path, config):
        (tmp_path / "README.md").write_text(
            "# My Project\n\nA well-documented project.\n"
        )
        (tmp_path / "ROADMAP.md").write_text(
            "# Roadmap\n\n## Phase 1\nBuild the thing.\n"
        )
        gaps = detect_doc_gaps(tmp_path, config)
        assert gaps == []

    def test_sorted_by_severity(self, tmp_path, config):
        (tmp_path / "mixed.md").write_text(
            "Line 1: {placeholder}\nLine 2: TBD\nLine 3: design TBD\n"
        )
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) >= 2
        # Critical should come first
        severities = [g.severity for g in gaps]
        assert severities == sorted(
            severities, key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s]
        )

    def test_max_items_cap(self, tmp_path, config):
        config["doc_gap_max_items"] = 3
        lines = "\n".join(f"Item {i}: TBD" for i in range(20))
        (tmp_path / "big.md").write_text(lines)
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) == 3

    def test_reports_correct_line_numbers(self, tmp_path, config):
        (tmp_path / "test.md").write_text("Line 1\nLine 2\nLine 3: TBD\nLine 4\n")
        gaps = detect_doc_gaps(tmp_path, config)
        assert len(gaps) == 1
        assert gaps[0].line == 3
        assert gaps[0].doc_path == "test.md"

    def test_doc_gap_serialization(self):
        gap = DocGap(
            doc_path="ROADMAP.md",
            line=10,
            pattern="TBD",
            text="Scoring: TBD",
            severity="warning",
        )
        d = gap.to_dict()
        restored = DocGap.from_dict(d)
        assert restored.doc_path == "ROADMAP.md"
        assert restored.line == 10
        assert restored.severity == "warning"


def test_is_excluded_fnmatch_pattern():
    assert _is_excluded("foo/bar.md", ["foo/*.md"]) is True


def test_is_likely_false_positive_short_placeholder():
    assert _is_likely_false_positive("{a}", "see {a} token") is True
