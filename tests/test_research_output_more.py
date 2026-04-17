"""Extra tests for aidlc.research_output."""

from aidlc.research_output import build_repair_prompt, is_permission_chatter


def test_build_repair_truncates_very_long_invalid_response():
    long = "x" * 13000
    prompt = build_repair_prompt("topic", "question?", long)
    assert "truncated" in prompt
    assert "```markdown" in prompt


def test_is_permission_chatter_empty_false():
    assert is_permission_chatter("") is False
    assert is_permission_chatter("   ") is False
