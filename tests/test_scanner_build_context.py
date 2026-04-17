"""Tests for scanner.build_context_prompt and detect_project_type."""

from pathlib import Path

from aidlc.scanner import ProjectScanner, detect_project_type


def test_scan_docs_skips_unreadable_file(tmp_path: Path):
    f = tmp_path / "secret.md"
    f.write_text("hi")
    f.chmod(0)
    try:
        s = ProjectScanner(tmp_path, {"doc_scan_patterns": ["*.md"]})
        s._scan_docs()
        assert getattr(s, "_skipped_docs_count", 0) >= 1
    finally:
        f.chmod(0o644)


def test_find_existing_issues_skips_unreadable(tmp_path: Path, monkeypatch):
    issues_dir = tmp_path / ".aidlc" / "issues"
    issues_dir.mkdir(parents=True)
    p = issues_dir / "bad.md"
    p.write_text("x")

    orig = Path.read_text

    def _read(self, *a, **kw):
        if self.resolve() == p.resolve():
            raise OSError("denied")
        return orig(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _read)
    s = ProjectScanner(tmp_path, {})
    s._find_existing_issues()
    assert getattr(s, "_skipped_issue_reads", 0) >= 1


def test_detect_project_type_glob_indicator(tmp_path: Path):
    (tmp_path / "App.xcodeproj").mkdir()
    t = detect_project_type(tmp_path)
    assert "swift/ios" in t


def test_scanner_audit_load_error(tmp_path: Path):
    (tmp_path / ".aidlc").mkdir(parents=True)
    (tmp_path / ".aidlc" / "audit_result.json").write_text("{")
    s = ProjectScanner(tmp_path, {})
    r = s.scan()
    assert r["audit_result"] is None
    assert r["scan_warnings"]["audit_result_load_errors"] >= 1


def test_build_context_truncates_when_over_char_budget(tmp_path: Path):
    s = ProjectScanner(tmp_path, {"max_context_chars": 50})
    scan = {
        "project_type": "python",
        "total_docs": 3,
        "structure_summary": "## Project Structure\n- a",
        "doc_files": [
            {"path": "a.md", "content": "A" * 40, "size": 40},
            {"path": "b.md", "content": "B" * 40, "size": 40},
        ],
        "existing_issues": [],
        "scan_warnings": {
            "skipped_docs": 1,
            "skipped_issue_reads": 0,
            "audit_result_load_errors": 0,
        },
    }
    text = s.build_context_prompt(scan)
    assert "Scanner degraded reads" in text
    assert "more docs not shown" in text


def test_build_context_audit_sections_and_tech_debt_overflow(tmp_path: Path):
    s = ProjectScanner(tmp_path, {})
    tech_debt = [
        {"file": f"f{i}.py", "line": i, "type": "todo", "text": "fixme"} for i in range(15)
    ]
    scan = {
        "project_type": "python",
        "total_docs": 0,
        "structure_summary": "s",
        "doc_files": [],
        "existing_issues": [],
        "scan_warnings": {},
        "audit_result": {
            "depth": "deep",
            "frameworks": ["django"],
            "entry_points": ["manage.py"],
            "modules": [{"name": "app", "role": "web", "file_count": 2, "line_count": 500}],
            "source_stats": {"total_files": 10, "total_lines": 2000},
            "test_coverage": {"estimated_coverage": "40%", "test_files": 1, "test_functions": 3},
            "features": ["auth"],
            "tech_debt": tech_debt,
        },
    }
    text = s.build_context_prompt(scan)
    assert "Code Audit Findings" in text
    assert "Test coverage" in text
    assert "and 5 more" in text


def test_parse_issue_non_issue_stem_returns_none(tmp_path: Path):
    s = ProjectScanner(tmp_path, {})
    p = tmp_path / "note.md"
    p.write_text("# Hi")
    assert s._parse_issue_markdown(p, "# Hi") is None


def test_build_context_issue_line_without_title(tmp_path: Path):
    s = ProjectScanner(tmp_path, {})
    scan = {
        "project_type": "python",
        "total_docs": 0,
        "structure_summary": "s",
        "doc_files": [],
        "existing_issues": [
            {
                "path": "issues/x.md",
                "parsed_issue": {
                    "id": "ISSUE-9",
                    "title": "",
                    "status": "open",
                    "priority": "low",
                },
            }
        ],
        "scan_warnings": {},
    }
    text = s.build_context_prompt(scan)
    assert "ISSUE-9" in text
    assert "issues/x.md" in text


def test_build_context_existing_issues_overflow_and_long_title(tmp_path: Path):
    s = ProjectScanner(tmp_path, {})
    long_title = "T" * 120
    issues = []
    for i in range(28):
        issues.append(
            {
                "path": f".aidlc/issues/ISSUE-{i:03d}.md",
                "content": "",
                "parsed_issue": {
                    "id": f"ISSUE-{i:03d}",
                    "title": long_title if i == 0 else f"Short {i}",
                    "status": "pending",
                    "priority": "high",
                },
            }
        )
    scan = {
        "project_type": "python",
        "total_docs": 0,
        "structure_summary": "s",
        "doc_files": [],
        "existing_issues": issues,
        "scan_warnings": {},
    }
    text = s.build_context_prompt(scan)
    assert "more issues" in text
    assert "..." in text
