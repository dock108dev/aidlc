"""Finalizer-orchestration helpers used by the Implementer mid-run.

Extracted from `implementer.py` so the orchestrator stays focused on the
issue loop. Three concerns live here:

  * ``should_run_periodic_cleanup`` — does the current cycle count match
    the configured cadence?
  * ``run_periodic_cleanup`` — drive the Finalizer with the opted-in
    cleanup subset (default: abend + cleanup), then restore the
    implementer phase.
  * ``run_finalize_before_push_if_enabled`` — run the full finalize_passes
    set before an autosync commit/push.

Each function takes the implementer instance and reads the same
``cleanup_passes_periodic``/``cleanup_passes_every_cycles``/
``autosync_finalize_before_push``/``config``/``state`` attributes the
original methods used.
"""

from __future__ import annotations

from .models import RunPhase
from .state_manager import save_state


def should_run_periodic_cleanup(impl) -> bool:
    """True when this implementation cycle should trigger a periodic cleanup pass."""
    if impl.cleanup_passes_every_cycles <= 0:
        return False
    if not impl.config.get("finalize_enabled", True) or impl.config.get("dry_run"):
        return False
    if not impl.cleanup_passes_periodic:
        return False
    return impl.state.implementation_cycles > 0 and (
        impl.state.implementation_cycles % impl.cleanup_passes_every_cycles == 0
    )


def _drive_finalizer(impl, passes) -> None:
    """Build a Finalizer, run the requested passes, and restore the phase."""
    # Local import — Finalizer pulls in heavyweight deps; keep the module load lazy.
    from .finalizer import Finalizer

    finalizer = Finalizer(
        impl.state,
        impl.run_dir,
        impl.config,
        impl.cli,
        impl.project_context,
        impl.logger,
    )
    finalizer.run(passes=passes)
    impl.state.phase = RunPhase.IMPLEMENTING
    save_state(impl.state, impl.run_dir)


def run_periodic_cleanup(impl) -> None:
    """Run the periodic-cleanup subset of finalization passes mid-run.

    Independent of autosync. Drives the same Finalizer entry point but
    with the opted-in subset (default: abend + cleanup).
    """
    cycle = impl.state.implementation_cycles
    passes = list(impl.cleanup_passes_periodic)
    impl.logger.info(
        f"Periodic cleanup at implementation cycle {cycle} (passes={', '.join(passes)})"
    )
    _drive_finalizer(impl, passes)


def run_finalize_before_push_if_enabled(impl) -> None:
    """Run full finalization passes (same as end-of-run) before commit/push."""
    if not impl.config.get("finalize_enabled", True):
        return
    if not impl.autosync_finalize_before_push:
        return
    if impl.config.get("dry_run"):
        return

    passes = impl.config.get("finalize_passes")
    c = impl.state.implementation_cycles
    impl.logger.info(
        f"Pre-autosync finalization at implementation cycle {c} (finalize_passes; then commit/push)"
    )
    _drive_finalizer(impl, passes)
