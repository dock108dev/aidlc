"""Configuration parsing for the implementation engine."""

from __future__ import annotations

from pathlib import Path


def apply_config(impl, config: dict) -> None:
    """Populate Implementer attributes derived directly from configuration."""
    impl.project_root = Path(config["_project_root"])
    impl.test_command = config.get("run_tests_command")
    impl.max_attempts = config.get("max_implementation_attempts", 3)
    impl.test_timeout = config.get("test_timeout_seconds", 300)
    impl.max_impl_context_chars = config.get("max_implementation_context_chars", 9000)
    impl.escalate_on_retry = config.get("implementation_escalate_on_retry", True)
    impl.complexity_ac_threshold = max(
        1,
        int(config.get("implementation_complexity_acceptance_criteria_threshold", 6)),
    )
    impl.complexity_dep_threshold = max(
        1, int(config.get("implementation_complexity_dependencies_threshold", 3))
    )
    impl.complexity_description_threshold = max(
        200,
        int(config.get("implementation_complexity_description_chars_threshold", 2500)),
    )
    default_labels = [
        "architecture",
        "security",
        "migration",
        "refactor-core",
        "cross-cutting",
    ]
    raw_complexity_labels = config.get(
        "implementation_complexity_labels",
        default_labels,
    )
    impl.complexity_labels = {
        str(label).strip().lower() for label in raw_complexity_labels if str(label).strip()
    }
    impl.issues_dir = Path(config["_issues_dir"])

    impl.autosync_enabled = bool(config.get("autosync_enabled", True))
    impl.autosync_every_cycles = max(
        1, int(config.get("autosync_every_implementation_cycles", 25) or 25)
    )
    impl.autosync_finalize_before_push = bool(config.get("autosync_finalize_before_push", True))
    impl.autosync_push_remote = bool(config.get("autosync_push_remote", True))
    impl.autosync_issue_status_sync = bool(config.get("autosync_issue_status_sync", True))
    impl.autosync_commit_message_template = str(
        config.get(
            "autosync_commit_message_template",
            "aidlc: autosync after implementation cycle {cycle}",
        )
    )
    impl.autosync_prune_enabled = bool(config.get("autosync_prune_enabled", True))
    impl.autosync_runs_to_keep = max(1, int(config.get("autosync_runs_to_keep", 5) or 5))
    impl.autosync_keep_provider_outputs = max(
        1, int(config.get("autosync_keep_provider_outputs", 200) or 200)
    )

    # Cleanup cadence is independent of autosync, which only handles commit/push.
    impl.cleanup_passes_every_cycles = max(
        0, int(config.get("cleanup_passes_every_cycles", 10) or 0)
    )
    raw_periodic_passes = config.get("cleanup_passes_periodic", ["cleanup"])
    impl.cleanup_passes_periodic = [
        str(p).strip().lower() for p in raw_periodic_passes if str(p).strip()
    ]
    impl.stop_on_all_models_token_exhausted = bool(
        config.get("stop_on_all_models_token_exhausted", True)
    )
