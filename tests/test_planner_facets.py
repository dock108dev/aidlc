"""Tests for the faceted-planning extension.

The planner now runs a fixed sequence of product-feedback facet cycles
(functionality, usability, design, new features) after the general
planning pass goes quiet, before falling into verify. These tests pin:

- The full sequence runs end-to-end (general → 4 facets → verify → done).
- A productive facet cycle still advances; verify is not triggered early.
- The facet-scoped instruction text contains the facet's name, checklist,
  and the heavy update-bias language that distinguishes it from the
  general pass.
- ``planning_facets_enabled: False`` reverts to the legacy
  single-quiet-cycle-then-verify behavior.
"""

import json
import logging
from unittest.mock import MagicMock

import pytest
from aidlc.models import Issue, RunState
from aidlc.planner import Planner
from aidlc.planner_text import (
    FACETS,
    PLANNING_INSTRUCTIONS,
    Facet,
    planning_instructions_faceted,
)


@pytest.fixture
def logger():
    return logging.getLogger("test_planner_facets")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(tmp_path / ".aidlc"),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,
        "max_consecutive_failures": 3,
        "finalization_budget_percent": 10,
        "dry_run": False,
        "max_planning_cycles": 0,
    }


def _wrap(payload: dict) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


def _empty_response() -> str:
    return _wrap({"frontier_assessment": "nothing", "actions": [], "cycle_notes": ""})


def _create_response(issue_id: str, title: str = "Surface gap") -> str:
    return _wrap(
        {
            "frontier_assessment": "gap",
            "actions": [
                {
                    "action_type": "create_issue",
                    "issue_id": issue_id,
                    "title": title,
                    "description": "desc",
                    "priority": "medium",
                    "acceptance_criteria": ["ok"],
                }
            ],
            "cycle_notes": "",
        }
    )


def _make_cli_result(output: str) -> dict:
    return {
        "success": True,
        "output": output,
        "error": None,
        "failure_type": None,
        "duration_seconds": 1.0,
        "retries": 0,
    }


def _seed_state() -> RunState:
    state = RunState(run_id="t-facets", config_name="default")
    state.plan_budget_seconds = 3600
    state.issues_created = 1
    issue = Issue(id="ISSUE-001", title="Seed", description="seed", acceptance_criteria=["ok"])
    state.update_issue(issue)
    return state


class TestFacetTaxonomyText:
    """The instruction text the model sees for a facet cycle."""

    def test_four_product_feedback_facets(self):
        names = [facet.name for facet in FACETS]
        assert len(FACETS) == 4
        # Names that match black-box product-feedback BRAINDUMPs, not
        # engineering-quality lenses.
        assert "Functionality" in names
        assert any("usability" in name.lower() for name in names)
        assert any("design" in name.lower() for name in names)
        assert any("feature" in name.lower() for name in names)

    @pytest.mark.parametrize("facet", FACETS, ids=lambda f: f.slug)
    def test_each_facet_prompt_contains_name_checklist_and_update_bias(self, facet: Facet):
        text = planning_instructions_faceted(facet)
        # Facet name appears in the scoped header.
        assert facet.name in text
        # Each checklist bullet is rendered verbatim.
        for bullet in facet.checklist:
            assert bullet in text
        # Exclusion clause directs other-facet noise elsewhere.
        assert facet.exclusion in text
        # Heavy update-bias language is the load-bearing distinction from
        # the general pass.
        lower = text.lower()
        assert "update_issue" in lower
        assert "create_issue" in lower
        assert "prefer" in lower
        # Faceted text wraps the standard PLANNING_INSTRUCTIONS so the
        # mode-aware / findings-as-reference guidance is preserved.
        assert PLANNING_INSTRUCTIONS in text


class TestFacetLoopSequencing:
    """The loop walks general → 4 facets → verify → done on quiet cycles."""

    def test_quiet_session_walks_full_facet_sequence(self, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = _make_cli_result(_empty_response())
        config["max_planning_cycles"] = 20
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()

        state = _seed_state()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()

        # 1 general + 4 facets + 1 verify = 6 cycles when every cycle is empty.
        assert state.planning_cycles == 6
        assert "verify" in (state.stop_reason or "").lower()
        # Final state: facet sequence drained, verify already cleared.
        assert planner._current_facet is None
        assert planner._facets_remaining == []
        assert planner._general_pass_done is True
        assert planner._verify_mode is False

    def test_facet_instructions_appear_in_prompts_in_order(self, config, logger, tmp_path):
        """During the facet phase, _planning_instructions returns the
        facet-scoped wrapper for the active facet, in the canonical order."""
        cli = MagicMock()
        cli.execute_prompt.return_value = _make_cli_result(_empty_response())
        config["max_planning_cycles"] = 20
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()

        state = _seed_state()
        planner = Planner(state, run_dir, config, cli, "context", logger)

        # Capture which facet was active for each call to
        # _planning_instructions, in call order.
        call_order: list[str | None] = []
        original = planner._planning_instructions

        def spy() -> str:
            current = planner._current_facet
            call_order.append(current.slug if current else None)
            return original()

        planner._planning_instructions = spy  # type: ignore[assignment]
        planner.run()

        # General pass uses no facet; the 4 facet cycles use facets in order;
        # the verify cycle also calls _planning_instructions (verify text is
        # appended on top of the planning text, not a replacement) with no
        # active facet — so the trailing entry is None again.
        expected = [None, "functionality", "usability", "design", "new_features", None]
        assert call_order == expected

    def test_productive_facet_cycle_advances_without_triggering_verify(
        self, config, logger, tmp_path
    ):
        """A productive facet cycle (any new issue) must NOT short-circuit
        into verify — it advances to the next facet."""
        cli = MagicMock()
        cli.execute_prompt.side_effect = [
            # Cycle 1: general → empty (advance to functionality)
            _make_cli_result(_empty_response()),
            # Cycle 2: functionality → empty (advance to usability)
            _make_cli_result(_empty_response()),
            # Cycle 3: usability → 1 new issue (advance to design)
            _make_cli_result(_create_response("ISSUE-002")),
            # Cycle 4: design → empty (advance to new_features)
            _make_cli_result(_empty_response()),
            # Cycle 5: new_features → empty (facets exhausted; verify next)
            _make_cli_result(_empty_response()),
            # Cycle 6: verify → empty → done
            _make_cli_result(_empty_response()),
        ]
        config["max_planning_cycles"] = 20
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        issues_dir = tmp_path / ".aidlc" / "issues"
        issues_dir.mkdir(parents=True, exist_ok=True)

        state = _seed_state()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()

        # 6 cycles total (productive facet cycle did not trigger early
        # verify). New issue from facet cycle 3 was filed.
        assert state.planning_cycles == 6
        assert state.issues_created == 2
        assert any(d["id"] == "ISSUE-002" for d in state.issues)
        assert "verify" in (state.stop_reason or "").lower()


class TestFacetDisableFlag:
    """Opting out via planning_facets_enabled=False restores legacy flow."""

    def test_disabled_flag_skips_facets_entirely(self, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = _make_cli_result(_empty_response())
        config["max_planning_cycles"] = 20
        config["planning_facets_enabled"] = False
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()

        state = _seed_state()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()

        # Legacy behavior: 1 general (empty) + 1 verify (empty) = 2 cycles.
        assert state.planning_cycles == 2
        assert planner._facets_remaining == []
        assert planner._current_facet is None
        assert "verify" in (state.stop_reason or "").lower()
