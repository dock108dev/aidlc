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
