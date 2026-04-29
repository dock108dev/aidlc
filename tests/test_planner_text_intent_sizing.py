"""Regression pins for the mode-aware planner instructions.

These tests don't try to assert what the LLM does (impossible without a
live model); they pin the *prompt text* that drives the model. If a
future refactor accidentally drops a load-bearing phrase, these tests
flag it before the cost shows up as 6-issue plans on 1000-line vision
BRAINDUMPs.

Background: a real run shipped 6 issues for a vision-flavored BRAINDUMP
because the model treated ``findings.md`` as a verdict that the named
systems were "already satisfied" rather than as reference for *how* to
implement BRAINDUMP intent. The instruction text now explicitly frames:

- Findings = reference for *how* (file locations, options, best practices),
  not a verdict on *whether* work is needed.
- BRAINDUMP tone picks the mode: vision/big-build vs scoped-fix.
- Issues are living plans — use update_issue to deepen across cycles.
"""

from types import SimpleNamespace

from aidlc.planner_helpers import _render_foundation_docs_section
from aidlc.planner_text import (
    PLANNING_INSTRUCTIONS,
    PLANNING_INSTRUCTIONS_VERSION,
    VERIFY_INSTRUCTIONS,
)


class TestPlanningInstructionsModeAware:
    def test_version_bumped_past_v7(self):
        # Cache stability: when instructions change materially the version
        # must bump so cached planning prompts don't mask the change.
        assert PLANNING_INSTRUCTIONS_VERSION != "2026-04-25-v7"

    def test_findings_is_reference_for_how_not_verdict(self):
        text = PLANNING_INSTRUCTIONS.lower()
        # The reframe: findings answers "how" / where / options, not "whether".
        assert "findings" in text
        assert "how" in text
        # Don't accept findings as a verdict that work isn't needed.
        assert "verdict" in text or "whether" in text

    def test_mode_aware_language_present(self):
        text = PLANNING_INSTRUCTIONS.lower()
        # The two modes are named explicitly so the model can classify.
        assert "vision" in text or "big-build" in text or "big build" in text
        assert "scoped-fix" in text or "scoped fix" in text

    def test_redesign_signals_named(self):
        text = PLANNING_INSTRUCTIONS.lower()
        # The signal words for vision/redesign mode.
        assert "rebuild" in text
        assert "big bang" in text or "recompose" in text

    def test_issues_are_living_plans(self):
        text = PLANNING_INSTRUCTIONS.lower()
        # Issues evolve across cycles — update_issue is first-class.
        assert "update_issue" in text or "update existing issues" in text
        assert "living" in text or "across cycles" in text

    def test_old_prefer_fewer_default_removed(self):
        # The old "prefer fewer, higher-quality" default biased toward
        # minimal-issue plans regardless of BRAINDUMP scope. Make sure
        # we didn't accidentally restore it.
        assert "prefer fewer, higher-quality" not in PLANNING_INSTRUCTIONS

    def test_sizing_follows_mode(self):
        text = PLANNING_INSTRUCTIONS.lower()
        # The sizing rule explicitly ties count to mode.
        assert "scoped-fix" in text or "scoped fix" in text
        # Vision-mode BRAINDUMPs get more issues.
        assert "10" in text or "ten" in text  # the order-of-magnitude hint


class TestVerifyInstructionsModeAware:
    def test_mode_classification_step(self):
        text = VERIFY_INSTRUCTIONS.lower()
        # Verify cycle re-classifies the BRAINDUMP mode before declaring
        # complete — that's the gate that stops 6-issue completions on
        # vision BRAINDUMPs.
        assert "mode" in text
        assert "vision" in text or "big-build" in text or "big build" in text
        assert "scoped-fix" in text or "scoped fix" in text

    def test_findings_reference_framing_in_verify(self):
        text = VERIFY_INSTRUCTIONS.lower()
        # Reminder during verify: findings is reference, not verdict.
        assert "reference" in text and "how" in text

    def test_completion_reason_must_name_mode_and_mapping(self):
        text = VERIFY_INSTRUCTIONS
        # Example shows mode classification + issue→intent-item mapping.
        assert "Mode:" in text
        assert "ISSUE-" in text  # example references actual issue IDs
        # Bundling is allowed but must name the mapping.
        assert "bundled" in text.lower() or "mapping" in text.lower()

    def test_old_rigid_ratio_gate_removed(self):
        # The earlier draft had hard 60%/150% ratio bands. The user
        # course-corrected that those are too rigid; mode-aware coverage
        # check replaced them.
        assert "0.6 ×" not in VERIFY_INSTRUCTIONS
        assert "60%" not in VERIFY_INSTRUCTIONS
        assert "150%" not in VERIFY_INSTRUCTIONS


class TestRenderedBraindumpSectionGuards:
    def _render(self, content: str) -> str:
        planner = SimpleNamespace(doc_files=[{"path": "BRAINDUMP.md", "content": content}])
        parts = _render_foundation_docs_section(planner)
        assert parts, "expected non-empty render for a non-empty BRAINDUMP"
        return "".join(parts)

    def test_empty_braindump_renders_nothing(self):
        # Preserve existing behavior: no BRAINDUMP doc → empty list.
        assert _render_foundation_docs_section(SimpleNamespace(doc_files=[])) == []
        assert (
            _render_foundation_docs_section(
                SimpleNamespace(doc_files=[{"path": "BRAINDUMP.md", "content": "  "}])
            )
            == []
        )

    def test_findings_is_reference_not_verdict_in_rendered_section(self):
        rendered = self._render("# Big Bang Pass\n\nRebuild the experience.").lower()
        # The reframe: findings answers "how" / "where" / "options".
        assert "reference for *how*" in rendered or "reference for how" in rendered
        # And it explicitly is not a verdict.
        assert "verdict" in rendered

    def test_mode_classification_in_rendered_section(self):
        rendered = self._render("# Big Bang Pass\n\nRebuild the experience.").lower()
        # The header instructs the model to read tone and pick mode.
        assert "tone" in rendered or "mode" in rendered
        assert "vision" in rendered or "big-build" in rendered or "big build" in rendered
        assert "scoped-fix" in rendered or "scoped fix" in rendered

    def test_living_issues_language_in_rendered_section(self):
        rendered = self._render("Just a small bug list.\n").lower()
        assert "update_issue" in rendered or "update existing issues" in rendered
        assert "living" in rendered or "across cycles" in rendered or "deepen" in rendered

    def test_braindump_full_content_still_embedded(self):
        # Sanity: the full BRAINDUMP body still ships in the prompt.
        marker = "UNIQUEMARKER_TEST_RENDER_42"
        rendered = self._render(f"# Hello\n\nIntent line {marker}.\n")
        assert marker in rendered
