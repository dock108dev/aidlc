"""Ensure plan_templates prompts are valid format strings."""

from aidlc.plan_templates import (
    ARCHITECTURE_GENERATION_PROMPT,
    CLAUDE_MD_GENERATION_PROMPT,
    DESIGN_GENERATION_PROMPT,
    REFINEMENT_SYSTEM_PROMPT,
    RESEARCH_TRIGGER_PROMPT,
    ROADMAP_GENERATION_PROMPT,
)

_VARS = {
    "project_name": "P",
    "one_liner": "A short description of the project for tests.",
    "project_type": "app",
    "tech_stack": "Python",
    "target_audience": "devs",
    "mvp_definition": "mvp",
    "constraints": "none",
    "inspiration": "inspo",
    "core_features": "- feat\n",
    "phases": "phase1",
    "existing_context": "",
    "research_needs": "need apis",
}


def test_roadmap_prompt_formats():
    s = ROADMAP_GENERATION_PROMPT.format(**_VARS)
    assert "P" in s and "ROADMAP" in s


def test_architecture_prompt_formats():
    s = ARCHITECTURE_GENERATION_PROMPT.format(**_VARS)
    assert "ARCHITECTURE" in s


def test_design_prompt_formats():
    s = DESIGN_GENERATION_PROMPT.format(**_VARS)
    assert "DESIGN" in s


def test_claude_md_prompt_formats():
    s = CLAUDE_MD_GENERATION_PROMPT.format(**_VARS)
    assert "CLAUDE" in s


def test_research_trigger_formats():
    s = RESEARCH_TRIGGER_PROMPT.format(**_VARS)
    assert "JSON" in s


def test_refinement_system_formats():
    s = REFINEMENT_SYSTEM_PROMPT.format(project_name="MyProj")
    assert "MyProj" in s
