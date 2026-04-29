"""Tests for aidlc.finalizer module."""

import logging
from unittest.mock import MagicMock

import pytest
from aidlc.finalizer import Finalizer
from aidlc.models import RunPhase, RunState


@pytest.fixture
def logger():
    return logging.getLogger("test_finalizer")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_runs_dir": str(tmp_path / ".aidlc" / "runs"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "finalize_timeout_seconds": 60,
        "dry_run": True,
    }


@pytest.fixture
def state():
    s = RunState(run_id="test_finalize", config_name="default")
    s.total_issues = 10
    s.issues_implemented = 8
    s.issues_verified = 7
    s.issues_failed = 1
    return s


@pytest.fixture
def cli():
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "# Audit Report\n\nFindings here.",
        "error": None,
        "failure_type": None,
        "duration_seconds": 5.0,
        "retries": 0,
    }
    cli.timeout = 600
    return cli


class TestFinalizer:
    def test_runs_all_passes_by_default(self, state, config, cli, logger, tmp_path):
        """End-of-run finalization defaults to the canonical 5-pass sweep."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run()

        assert state.finalize_passes_completed == [
            "ssot",
            "security",
            "abend",
            "cleanup",
            "docs",
        ]

    def test_runs_selected_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["cleanup", "docs"])

        # Passes run in user-supplied order, not PASS_ORDER.
        assert state.finalize_passes_completed == ["cleanup", "docs"]

    def test_skips_invalid_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["nonexistent", "docs"])

        assert state.finalize_passes_completed == ["docs"]

    def test_periodic_subset_runs(self, state, config, cli, logger, tmp_path):
        """Periodic cleanup runs only the subset opted into; default is
        abend + cleanup."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["abend", "cleanup"])

        assert state.finalize_passes_completed == ["abend", "cleanup"]

    def test_handles_failed_pass(self, state, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": False,
            "output": "",
            "error": "timeout",
            "failure_type": "transient",
            "duration_seconds": 60.0,
            "retries": 2,
        }
        cli.timeout = 600
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "context", logger)
        finalizer.run(passes=["docs"])

        # Failed pass should NOT be in completed list
        assert "docs" not in state.finalize_passes_completed

    def test_writes_futures_note(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])

        futures_path = tmp_path / "AIDLC_FUTURES.md"
        assert futures_path.exists()
        content = futures_path.read_text()
        assert "AIDLC Futures" in content
        assert "test_finalize" in content

    def test_creates_audit_dir(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])

        audit_dir = tmp_path / "docs" / "audits"
        assert audit_dir.exists()

    def test_saves_raw_output(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["cleanup"])

        output_file = run_dir / "claude_outputs" / "finalize_cleanup.md"
        assert output_file.exists()

    def test_tracks_requested_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs", "cleanup"])

        assert state.finalize_passes_requested == ["docs", "cleanup"]

    def test_sets_finalizing_phase(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])

        assert state.phase == RunPhase.FINALIZING

    def test_second_run_replaces_completed_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])
        assert state.finalize_passes_completed == ["docs"]
        finalizer.run(passes=["cleanup"])
        assert state.finalize_passes_completed == ["cleanup"]

    def test_empty_passes_logs_warning(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "context", logger)
        finalizer.run(passes=["invalid_only"])

        assert state.finalize_passes_completed == []


class TestPassPrompts:
    """Each pass prompt declares the actionability contract and is
    registered in the finalizer."""

    def test_all_five_passes_registered(self):
        from aidlc.finalize_prompts import PASS_DESCRIPTIONS, PASS_ORDER
        from aidlc.finalizer import PASS_PROMPTS

        expected = {"ssot", "security", "abend", "cleanup", "docs"}
        assert set(PASS_PROMPTS.keys()) == expected
        assert set(PASS_DESCRIPTIONS.keys()) == expected
        assert set(PASS_ORDER) == expected
        # PASS_ORDER places cleanup before docs (so docs sees a clean tree)
        assert PASS_ORDER.index("cleanup") < PASS_ORDER.index("docs")

    def test_every_prompt_carries_actionability_contract(self):
        from aidlc.finalizer import PASS_PROMPTS

        for name, prompt in PASS_PROMPTS.items():
            assert (
                "Actionability Contract" in prompt
            ), f"Pass '{name}' is missing the actionability contract"
            # Contract rejects bare TODOs by name.
            assert "TODO" in prompt
            # Contract names the report file the model must write into.
            assert "docs/audits/" in prompt

    def test_diff_aware_passes_have_diff_placeholder(self):
        """ssot/security/abend/cleanup all reason about branch changes; their
        prompts must accept a {diff_summary} substitution. docs is current-state
        only and intentionally omits it."""
        from aidlc.finalizer import DIFF_AWARE_PASSES, PASS_PROMPTS

        for name in DIFF_AWARE_PASSES:
            assert (
                "{diff_summary}" in PASS_PROMPTS[name]
            ), f"diff-aware pass '{name}' missing {{diff_summary}} placeholder"
        assert "{diff_summary}" not in PASS_PROMPTS["docs"]


class TestPeriodicCleanupHook:
    """The implementer fires periodic cleanup at the configured cadence."""

    def _make_implementer(self, every_cycles: int, periodic: list[str], cycles: int):
        from aidlc.implementer import Implementer

        # Bypass Implementer.__init__ — we only need the cadence attributes
        # and a tiny fake state for the predicate.
        impl = Implementer.__new__(Implementer)
        impl.config = {"finalize_enabled": True, "dry_run": False}
        impl.cleanup_passes_every_cycles = every_cycles
        impl.cleanup_passes_periodic = periodic
        impl.state = MagicMock()
        impl.state.implementation_cycles = cycles
        return impl

    def test_fires_on_multiples_of_cadence(self):
        from aidlc.implementer import Implementer

        impl = self._make_implementer(10, ["abend", "cleanup"], cycles=10)
        assert Implementer._should_run_periodic_cleanup(impl) is True

        impl.state.implementation_cycles = 20
        assert Implementer._should_run_periodic_cleanup(impl) is True

        impl.state.implementation_cycles = 30
        assert Implementer._should_run_periodic_cleanup(impl) is True

    def test_does_not_fire_off_cadence(self):
        from aidlc.implementer import Implementer

        impl = self._make_implementer(10, ["abend", "cleanup"], cycles=5)
        assert Implementer._should_run_periodic_cleanup(impl) is False
        impl.state.implementation_cycles = 12
        assert Implementer._should_run_periodic_cleanup(impl) is False

    def test_zero_cadence_disables_hook(self):
        from aidlc.implementer import Implementer

        impl = self._make_implementer(0, ["abend"], cycles=10)
        assert Implementer._should_run_periodic_cleanup(impl) is False

    def test_empty_periodic_list_disables_hook(self):
        from aidlc.implementer import Implementer

        impl = self._make_implementer(10, [], cycles=10)
        assert Implementer._should_run_periodic_cleanup(impl) is False

    def test_dry_run_disables_hook(self):
        from aidlc.implementer import Implementer

        impl = self._make_implementer(10, ["abend"], cycles=10)
        impl.config["dry_run"] = True
        assert Implementer._should_run_periodic_cleanup(impl) is False

    def test_finalize_disabled_disables_hook(self):
        from aidlc.implementer import Implementer

        impl = self._make_implementer(10, ["abend"], cycles=10)
        impl.config["finalize_enabled"] = False
        assert Implementer._should_run_periodic_cleanup(impl) is False

    def test_first_cycle_zero_does_not_fire(self):
        """implementation_cycles starts at 0; the predicate guards against
        firing before any work has happened."""
        from aidlc.implementer import Implementer

        impl = self._make_implementer(10, ["abend"], cycles=0)
        assert Implementer._should_run_periodic_cleanup(impl) is False


class TestConfigDefaults:
    def test_periodic_cadence_defaults(self):
        from aidlc.config import DEFAULTS

        assert DEFAULTS["cleanup_passes_every_cycles"] == 10
        assert DEFAULTS["cleanup_passes_periodic"] == ["abend", "cleanup"]
        # End-of-run still defaults to "all" passes
        assert DEFAULTS["finalize_passes"] is None
