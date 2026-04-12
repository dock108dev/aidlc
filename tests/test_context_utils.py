"""Tests for shared context parsing helpers."""

from aidlc.context_utils import parse_project_type


def test_parse_project_type_returns_value():
    context = "\n".join(
        [
            "Project Context",
            "Project type: python",
            "Other: value",
        ]
    )
    assert parse_project_type(context) == "python"


def test_parse_project_type_returns_empty_when_missing():
    context = "No type markers here"
    assert parse_project_type(context) == ""
