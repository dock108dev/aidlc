"""ISSUE-007: implementer prompt and JSON schema additions.

The implementation prompt now tells the agent to modify in place rather than
rewrite working systems, list existing tests, and populate
``existing_callers_checked`` in its JSON output. The JSON parser must accept
the new optional field without breaking back-compat.
"""

from __future__ import annotations

from aidlc.implementer_helpers import implementation_instructions
from aidlc.schemas import (
    IMPLEMENTATION_SCHEMA_DESCRIPTION,
    parse_implementation_result,
)


def test_prompt_contains_preserve_dont_rewrite_clauses():
    text = implementation_instructions(test_command="pytest -q")
    assert "modify in place" in text
    assert "Rewriting is a last resort" in text
    assert "list its existing tests" in text
    assert "regression, not progress" in text


def test_implementer_prompt_lists_research_files_when_present(tmp_path):
    """Research awareness: planner-emitted research lands in .aidlc/research/.

    The implementer must be told these files exist so it reads relevant ones
    instead of re-deriving content. Without this, the work the planner did
    via `research` actions is invisible at implementation time.
    """
    from unittest.mock import MagicMock

    from aidlc.implementer_helpers import build_implementation_prompt
    from aidlc.issue_model import Issue

    research_dir = tmp_path / ".aidlc" / "research"
    research_dir.mkdir(parents=True)
    (research_dir / "hole-layouts.md").write_text("# Hole layouts")
    (research_dir / "physics-tuning.md").write_text("# Physics")

    impl = MagicMock()
    impl.config = {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(tmp_path / ".aidlc"),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "implementation_completed_issues_max": 12,
    }
    impl.test_command = "pytest -q"
    impl.max_impl_context_chars = 12000
    impl.project_context = "ctx"
    impl.state.issues = []
    issue = Issue(id="ISSUE-001", title="t", description="d", acceptance_criteria=["AC"])

    prompt = build_implementation_prompt(impl, issue)
    assert "## Available Research" in prompt
    assert ".aidlc/research/hole-layouts.md" in prompt
    assert ".aidlc/research/physics-tuning.md" in prompt
    assert "read them first" in prompt


def test_implementer_prompt_omits_research_section_when_empty(tmp_path):
    """Don't pollute the prompt with an empty section on greenfield projects."""
    from unittest.mock import MagicMock

    from aidlc.implementer_helpers import build_implementation_prompt
    from aidlc.issue_model import Issue

    impl = MagicMock()
    impl.config = {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "implementation_completed_issues_max": 12,
    }
    impl.test_command = None
    impl.max_impl_context_chars = 12000
    impl.project_context = "ctx"
    impl.state.issues = []
    issue = Issue(id="ISSUE-001", title="t", description="d", acceptance_criteria=["AC"])

    prompt = build_implementation_prompt(impl, issue)
    assert "## Available Research" not in prompt


def test_schema_documents_existing_callers_checked():
    assert "existing_callers_checked" in IMPLEMENTATION_SCHEMA_DESCRIPTION


def test_parse_implementation_result_accepts_new_field():
    raw = """
some chatter
```json
{
  "issue_id": "ISSUE-042",
  "success": true,
  "summary": "added retry chain",
  "files_changed": ["aidlc/routing/engine.py"],
  "tests_passed": true,
  "notes": "",
  "existing_callers_checked": ["aidlc/planner.py:120", "aidlc/implementer.py:88"]
}
```
""".strip()
    result = parse_implementation_result(raw)
    assert result.issue_id == "ISSUE-042"
    assert result.existing_callers_checked == [
        "aidlc/planner.py:120",
        "aidlc/implementer.py:88",
    ]


def test_parse_implementation_result_back_compat_when_field_absent():
    """Existing implementations without the new field continue to parse."""
    raw = """
```json
{
  "issue_id": "ISSUE-001",
  "success": true,
  "summary": "x",
  "files_changed": [],
  "tests_passed": true,
  "notes": ""
}
```
""".strip()
    result = parse_implementation_result(raw)
    assert result.issue_id == "ISSUE-001"
    # Default empty list rather than missing attribute or None.
    assert result.existing_callers_checked == []


def test_parse_implementation_result_handles_null_field():
    """Models sometimes emit null instead of [] for empty arrays."""
    raw = """
```json
{
  "issue_id": "ISSUE-001",
  "success": true,
  "summary": "x",
  "files_changed": [],
  "tests_passed": true,
  "notes": "",
  "existing_callers_checked": null
}
```
""".strip()
    result = parse_implementation_result(raw)
    assert result.existing_callers_checked == []
