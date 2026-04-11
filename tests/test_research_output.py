from aidlc.research_output import (
    add_research_output_constraints,
    build_repair_prompt,
    is_permission_chatter,
)


def test_is_permission_chatter_detects_write_tool_text():
    text = (
        "The Write tool needs your permission approval to create the file. "
        "Could you approve the write permission when prompted?"
    )
    assert is_permission_chatter(text)


def test_is_permission_chatter_ignores_normal_research_doc():
    text = "# Findings\n\nThis document summarizes collision tuning."
    assert not is_permission_chatter(text)


def test_add_research_output_constraints_appends_guardrails():
    prompt = "Output as markdown."
    constrained = add_research_output_constraints(prompt)
    assert "Do NOT ask for write permissions." in constrained
    assert constrained.startswith(prompt)


def test_build_repair_prompt_includes_topic_and_question():
    prompt = build_repair_prompt("topic-a", "What should we do?", "bad output")
    assert "# Research: topic-a" in prompt
    assert "What should we do?" in prompt
    assert "bad output" in prompt
