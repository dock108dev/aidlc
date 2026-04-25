"""Config loader for AIDLC runner. Project-agnostic."""

import json
import os
from pathlib import Path

# Framework root (where aidlc package lives)
AIDLC_PKG_ROOT = Path(__file__).parent
CONFIGS_DIR = AIDLC_PKG_ROOT / "configs"

# Default configuration values
DEFAULTS = {
    "runtime_profile": "standard",  # standard | production
    "routing_strategy": "balanced",  # balanced | cheapest | best_quality | custom
    # Per implementation routing resolve: probability (0–1) to try Copilot/OpenAI before
    # max_capacity backends so budget CLIs get occasional usage without starving premium.
    "routing_impl_budget_explore_probability": 0.05,
    "routing_rate_limit_cooldown_seconds": 300,
    # Added on top of any provider-reported restore time (429/retry-after). Doubles per
    # consecutive rate limit on the same (provider, model): 1×, 2×, 4× … capped at 8× base.
    "routing_rate_limit_buffer_base_seconds": 3600,
    "providers": {  # provider enable/model configuration
        "claude": {
            "enabled": True,
            "cli_command": "claude",
            # High token-capacity backend: preferred for implementation; weighted ~20× on other phases.
            "max_capacity": True,
            "max_capacity_weight": 20,
            "default_model": "sonnet",
            "phase_models": {
                "planning": "sonnet",
                "research": "sonnet",
                "implementation": "sonnet",
                "implementation_complex": "opus",
                "finalization": "sonnet",
                "audit": "sonnet",
            },
            # Ordered list tried in sequence when a model returns "out of tokens"
            # before the entire provider is excluded (ISSUE-004). Empty list = legacy
            # behavior (provider excluded on first exhaustion).
            "model_fallback_chain": ["sonnet", "opus", "haiku"],
        },
        "copilot": {
            "enabled": False,
            "cli_command": "copilot",
            "default_model": "",
            "phase_models": {
                "planning": "",
                "research": "",
                "implementation": "",
                "implementation_complex": "",
                "finalization": "",
                "audit": "",
            },
            "model_fallback_chain": [],
        },
        "openai": {
            "enabled": False,
            "cli_command": "codex",
            "default_model": "gpt-5.4",
            "phase_models": {
                "planning": "gpt-5.4-mini",
                "research": "gpt-5.4-mini",
                "implementation": "gpt-5.4",
                "implementation_complex": "gpt-5.3-codex",
                "finalization": "gpt-5.4-mini",
                "audit": "gpt-5.4-mini",
            },
            "model_fallback_chain": ["gpt-5.4", "gpt-5.4-mini"],
        },
    },
    "plan_budget_hours": 4,
    "checkpoint_interval_minutes": 15,
    "dry_run": False,
    "claude_long_run_warn_seconds": 300,  # heartbeat-log cadence while Claude is still running
    # Hard timeout disabled by default — Claude CLI can legitimately run for an
    # hour+ on complex tasks, and stream-json gives us an activity signal so
    # "running" vs "stuck" is no longer just "elapsed time". Set > 0 if you
    # want a wall-clock escape hatch regardless of activity.
    "claude_hard_timeout_seconds": 0,
    # Stall detection (activity-based, uses stream-json line events as the
    # liveness signal):
    # - claude_stall_warn_seconds: flip the heartbeat log from INFO to WARNING
    #   once Claude has been silent for this long. Does not kill. Default 300s.
    # - claude_stall_kill_seconds: if > 0, kill the process after this much
    #   genuine silence. Disabled by default; opt in as a safety valve for
    #   unattended runs.
    "claude_stall_warn_seconds": 300,
    "claude_stall_kill_seconds": 0,
    "claude_timeout_grace_seconds": 30,  # wait for graceful Claude shutdown before force-kill
    # Telemetry/cost tracking:
    # - auto: use exact CLI-reported cost when available, otherwise estimate from token rates
    # - exact_only: track only exact cost values from CLI metadata
    # - estimate_only: always estimate from token rates
    "telemetry_cost_mode": "auto",
    # If false (default): do not add USD from telemetry_model_pricing_* (API list $/M ≠ subscription plans).
    "telemetry_estimate_usd": False,
    # Estimated USD per million tokens for fallback cost tracking.
    # These are budgeting estimates only and may differ from provider billing.
    "telemetry_model_pricing_usd_per_million_tokens": {
        "default": {
            "input": 3.0,
            "output": 15.0,
            "cache_creation_input": 3.75,
            "cache_read_input": 0.30,
        },
        "sonnet": {
            "input": 3.0,
            "output": 15.0,
            "cache_creation_input": 3.75,
            "cache_read_input": 0.30,
        },
        "opus": {
            "input": 15.0,
            "output": 75.0,
            "cache_creation_input": 18.75,
            "cache_read_input": 1.50,
        },
        "haiku": {
            "input": 0.8,
            "output": 4.0,
            "cache_creation_input": 1.0,
            "cache_read_input": 0.08,
        },
        # OpenAI GPT-5 family (current as of April 2026)
        "gpt-5.4": {
            "input": 2.50,
            "output": 15.0,
            "cache_creation_input": 2.50,
            "cache_read_input": 0.25,
        },
        "gpt-5.4-mini": {
            "input": 0.75,
            "output": 4.50,
            "cache_creation_input": 0.75,
            "cache_read_input": 0.075,
        },
        "gpt-5.4-nano": {
            "input": 0.20,
            "output": 1.25,
            "cache_creation_input": 0.20,
            "cache_read_input": 0.02,
        },
        "gpt-5.3-codex": {
            "input": 1.75,
            "output": 14.0,
            "cache_creation_input": 1.75,
            "cache_read_input": 0.175,
        },
        "gpt-4o": {
            "input": 2.5,
            "output": 10.0,
            "cache_creation_input": 2.5,
            "cache_read_input": 1.25,
        },
        "gpt-4o-mini": {
            "input": 0.15,
            "output": 0.6,
            "cache_creation_input": 0.15,
            "cache_read_input": 0.075,
        },
    },
    "retry_max_attempts": 2,
    "retry_base_delay_seconds": 30,
    "retry_max_delay_seconds": 300,
    "retry_backoff_factor": 2.0,
    "claude_service_outage_max_wait_seconds": 7200,  # keep retrying on 5xx/outage for up to 2h
    "max_consecutive_failures": 3,
    "diminishing_returns_window": 5,  # track last N cycles for diminishing returns
    # ISSUE-011: adaptive threshold = clamp(min, ceil(num_issues_so_far/10), max).
    # Floor (small projects use this), ceiling (very large projects).
    "planning_diminishing_returns_min_threshold": 3,
    "planning_diminishing_returns_max_threshold": 6,
    "finalization_budget_percent": 10,
    "planning_finalization_grace_cycles": 1,  # finalization cycles allowed after budget exhaustion
    "max_implementation_attempts": 3,
    "implementation_escalate_on_retry": True,  # escalate retries to complex implementation model
    # If true, issues that are Status=verified but have an empty Verification Result (common when
    # templates or planning copy mark verified without work) are re-opened as pending so the
    # implementation phase actually runs. Set false if you intentionally keep verified without prose.
    "implementation_reopen_verified_without_result": True,
    # If true, allow ISSUE implemented when full test cmd still fails but fix prompt documents
    # pre-existing unrelated failures and follow_up_documentation meets min length.
    "implementation_accept_pre_existing_suite_failures": True,
    "implementation_pre_existing_debt_min_chars": 40,
    # If fix response has no valid JSON, infer pre-existing debt from prose (models often skip JSON).
    "implementation_pre_existing_prose_heuristic": True,
    # After project-wide suite debt is accepted once, use targeted GUT/tests for implementation runs.
    "implementation_use_targeted_tests_when_suite_unstable": True,
    # Optional shell template; {gtest_paths} or {paths} = comma-separated res://... test .gd files.
    "implementation_targeted_test_command": None,
    "implementation_complexity_acceptance_criteria_threshold": 12,
    "implementation_complexity_dependencies_threshold": 5,
    "implementation_complexity_description_chars_threshold": 5000,
    "implementation_complexity_labels": [  # labels that force complex implementation model
        "architecture",
        "security",
        "migration",
        "refactor-core",
        "cross-cutting",
    ],
    "max_planning_cycles": 0,  # 0 = unlimited (dry-run defaults to 3)
    "max_implementation_cycles": 0,  # 0 = unlimited (dry-run defaults to 3)
    "run_tests_command": None,  # auto-detected if not set
    "test_timeout_seconds": 300,
    "max_doc_chars": 10000,
    "max_context_chars": 40000,
    # Implementation-phase prompt budgets. Separate from planning so the
    # implementer gets a tighter context (planning-only docs filtered out,
    # per-doc cap lower, audit/issue-index sections skipped). Drop these
    # numbers to shrink the implementation prompt without affecting planning.
    "max_implementation_context_chars": 9000,
    "implementation_max_doc_chars": 4000,
    "doc_scan_patterns": [
        "**/*.md",
        "**/*.txt",
        "**/*.rst",
    ],
    "doc_scan_exclude": [
        "node_modules/**",
        ".git/**",
        "venv/**",
        ".venv/**",
        "__pycache__/**",
        ".aidlc/**",
        "dist/**",
        "build/**",
    ],
    "implementation_allowed_paths": None,  # None = all paths allowed
    # Audit settings
    "audit_depth": "quick",  # default depth when --audit is used
    "audit_max_claude_calls": 10,  # cap provider calls during full audit
    "audit_max_source_chars_per_module": 15000,  # source chars sent to Claude per module
    "audit_source_extensions": [
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".rb",
    ],
    "audit_exclude_patterns": [
        "**/test*/**",
        "**/vendor/**",
        "**/node_modules/**",
        "**/.git/**",
    ],
    "audit_runtime_enabled": True,  # run build/unit/integration/e2e checks during full audit
    "audit_runtime_timeout_seconds": 600,  # timeout for each runtime audit command
    "audit_coverage_threshold_percent": 85,  # focus shifts to UI when >= threshold
    "audit_playwright_headless": True,  # enforce headless Playwright in runtime audit
    "audit_playwright_command_override": None,  # optional custom Playwright command
    # Research settings
    "research_max_scope_files": 10,  # max files to read per research action
    "research_max_source_chars": 15000,  # max chars per scope file
    "research_max_per_cycle": 2,  # max research actions per planning cycle
    "research_timeout_seconds": 900,  # 15 min timeout per research call
    # Doc-gap detection — opt-in. On mature repos, scanning every doc for TBD
    # markers and turning them into spurious planning issues creates noise.
    # Set to true on greenfield projects where doc gaps are real planning input.
    "doc_gap_detection_enabled": False,
    "doc_gap_max_items": 50,  # cap gaps passed to planner prompt (when enabled)
    # Resume (implementation-phase continuation)
    "resume_reconcile_enabled": True,  # git-grep heuristic when resuming past planning
    "planning_doc_min_chars": 800,  # minimum chars for "sufficient" planning docs
    # Context preparation
    "project_brief_max_chars": 20000,  # max size of generated project brief
    "phase_context_max_chars": 20000,  # max chars for phase-focused docs per cycle
    "max_planning_prompt_chars": 60000,  # total prompt budget per planning cycle
    "planning_issue_index_max_items": 15,  # max issues listed inline in planning prompt
    "planning_issue_index_include_all_until": 12,  # list all issues until this count
    "planning_last_cycle_notes_max_chars": 300,  # max chars from previous-cycle notes
    # Validation loop
    "validation_enabled": True,
    "strict_validation": False,  # if True, validation failures pause run
    "validation_allow_no_tests": True,  # if False, missing tests fail validation
    "fail_on_validation_incomplete": False,  # if True, incomplete validation pauses run
    "validation_max_cycles": 3,  # max test-fix iterations
    "validation_batch_size": 10,  # max fix issues per cycle
    "test_profile_mode": "progressive",  # unit → integration → e2e (stop on first fail)
    "e2e_test_command": None,  # override E2E test command
    "build_validation_command": None,  # override build command
    # Finalization
    "finalize_enabled": True,  # master switch for finalization
    "fail_on_final_test_failure": False,  # if True, failed final suite pauses run
    "strict_change_detection": False,  # if True, impl success requires verifiable changes
    "finalize_passes": None,  # None = all; or ["ssot", "docs"]
    "finalize_timeout_seconds": 900,  # 15 min per pass
    # Finalize prompts: full project_context can exceed CLI limits — cap with head+tail preserve
    "finalize_project_context_max_chars": 22000,
    # Implementation prompt: max prior completed issues listed (titles only); rest on disk
    "implementation_completed_issues_max": 6,
    # Implementation prompt: max docs/research/*.md filenames shown in the
    # "Available Research" index (implementer can still list the directory).
    "implementation_research_index_max": 15,
    # Patterns that classify a doc as planning-only (dropped from impl context)
    # or implementation-relevant (kept). Unmatched docs fall through to both.
    # Override per-project by setting the same key in .aidlc/config.json; set
    # "planning_only" to [] to disable filtering.
    "implementation_doc_phase_patterns": {
        "planning_only": [
            "BRAINDUMP*",
            "*ROADMAP*",
            "*VISION*",
            "*FUTURES*",
            "docs/roadmap.*",
            "planning/**",
            "rfcs/**",
        ],
        "implementation": [
            "README*",
            "ARCHITECTURE*",
            "DESIGN*",
            "CLAUDE.md",
            "docs/architecture.*",
            "docs/setup.*",
            "docs/testing.*",
            "docs/contributing.*",
            "docs/configuration*",
            "docs/style/**",
            "specs/**",
        ],
    },
    "planning_action_failure_ratio_threshold": 0.6,  # fail cycle if too many actions fail
    # Implementation autosync / resilience
    "autosync_enabled": True,
    "autosync_every_implementation_cycles": 25,
    # Run finalize_passes (same as end-of-run) before autosync commit/push on each interval.
    "autosync_finalize_before_push": True,
    "autosync_push_remote": True,
    "autosync_commit_message_template": "aidlc: autosync after implementation cycle {cycle}",
    "autosync_issue_status_sync": True,
    "autosync_prune_enabled": True,
    "autosync_runs_to_keep": 5,
    "autosync_keep_claude_outputs": 200,
    # Stop run cleanly when router confirms token exhaustion across all models/providers
    "stop_on_all_models_token_exhausted": True,
    # ISSUE-009: when implementation stops with work remaining, do NOT auto-run
    # ssot/abend/cleanup finalization passes by default. Set to true to restore
    # the prior behavior. The new default exits cleanly with a STOP REASON +
    # RESUME WITH log so you can pick up after the underlying issue resolves.
    "implementation_finalize_on_early_stop": False,
}


def _merge_user_config(config: dict, user_config: dict) -> None:
    """Merge user config into config dict, deep-merging the providers sub-dict.

    A shallow config.update() would replace the entire 'providers' dict, losing
    defaults for any provider the user didn't fully specify (e.g. cli_command,
    default_model, phase_models).  This function merges top-level keys normally
    but deep-merges each per-provider dict so that only the user-specified keys
    are overwritten.

    Side effect: stashes the user's raw per-provider keys at
    ``config["_user_provider_overrides"][provider_id]`` so model-resolution
    code (``routing.context.resolve_model_for_phase``) can tell user-set from
    DEFAULT values. This is what makes a user-set ``default_model`` win over a
    DEFAULT ``phase_models[phase]`` entry without forcing users to override
    every phase explicitly (ISSUE-003).
    """
    overrides = config.setdefault("_user_provider_overrides", {})
    for key, value in user_config.items():
        if (
            key == "providers"
            and isinstance(value, dict)
            and isinstance(config.get("providers"), dict)
        ):
            for provider_id, provider_cfg in value.items():
                if isinstance(provider_cfg, dict) and isinstance(
                    config["providers"].get(provider_id), dict
                ):
                    # Record which fields the user explicitly set so later
                    # precedence checks can prefer them over DEFAULTS without
                    # forcing the user to redeclare every phase_models entry.
                    overrides[provider_id] = {
                        "default_model": provider_cfg.get("default_model"),
                        "phase_models": dict(provider_cfg.get("phase_models") or {}),
                    }
                    # Deep-merge: user values override defaults, but missing keys keep defaults
                    merged = dict(config["providers"][provider_id])
                    # Also deep-merge phase_models if both sides have it
                    if "phase_models" in provider_cfg and isinstance(
                        provider_cfg["phase_models"], dict
                    ):
                        base_phases = dict(merged.get("phase_models") or {})
                        base_phases.update(provider_cfg["phase_models"])
                        merged.update(provider_cfg)
                        merged["phase_models"] = base_phases
                    else:
                        merged.update(provider_cfg)
                    config["providers"][provider_id] = merged
                else:
                    config["providers"][provider_id] = provider_cfg
                    overrides[provider_id] = {
                        "default_model": provider_cfg.get("default_model")
                        if isinstance(provider_cfg, dict)
                        else None,
                        "phase_models": dict(provider_cfg.get("phase_models") or {})
                        if isinstance(provider_cfg, dict)
                        else {},
                    }
        else:
            config[key] = value


def write_default_config(aidlc_dir: Path, detected_overrides: dict | None = None) -> Path:
    """Write a default .aidlc/config.json using the canonical defaults.

    This is the single authoritative place that writes the initial project config,
    replacing the two independent writers that previously existed in precheck.py
    and cli_commands.py.

    Args:
        aidlc_dir: The .aidlc/ directory path (will be created if missing).
        detected_overrides: Optional dict of auto-detected values to merge in
                           (e.g., from config_detect.detect_config()).

    Returns:
        Path to the written config file.
    """
    aidlc_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("issues", "runs", "reports"):
        (aidlc_dir / subdir).mkdir(exist_ok=True)

    config_path = aidlc_dir / "config.json"
    if not config_path.exists():
        default_config: dict = {
            "plan_budget_hours": 4,
            "checkpoint_interval_minutes": 15,
            "routing_strategy": "balanced",
            "providers": {
                "claude": {
                    "enabled": True,
                    "cli_command": "claude",
                    "default_model": "sonnet",
                    "accounts": [
                        {
                            "id": "default",
                            "display_name": "Claude (default)",
                            "tier": "unknown",
                            "role_tags": ["primary"],
                            "enabled": True,
                        }
                    ],
                },
                "copilot": {
                    "enabled": False,
                    "cli_command": "copilot",
                    "default_model": "",
                    "accounts": [],
                },
                "openai": {
                    "enabled": False,
                    "cli_command": "codex",
                    "default_model": "gpt-4o",
                    "accounts": [],
                },
            },
            "max_implementation_attempts": 3,
            "run_tests_command": None,
        }
        if detected_overrides:
            for key, value in detected_overrides.items():
                if not key.startswith("_") and value is not None:
                    default_config[key] = value
        with open(config_path, "w") as f:
            json.dump(default_config, f, indent=2)
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            pass

    # Add .gitignore entries
    project_root = aidlc_dir.parent
    gitignore = project_root / ".gitignore"
    ignore_entry = "\n# AIDLC working directory\n.aidlc/runs/\n.aidlc/reports/\n"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".aidlc/" not in content:
            with open(gitignore, "a") as f:
                f.write(ignore_entry)
    else:
        gitignore.write_text(ignore_entry.lstrip())

    return config_path


def load_config(config_path: str | None = None, project_root: str | None = None) -> dict:
    """Load and validate config. Merges defaults with user config."""
    config = dict(DEFAULTS)
    # Always present so consumers can do ``config["_user_provider_overrides"].get(pid)``
    # without checking for the key first. Populated by ``_merge_user_config`` when a
    # user config is loaded.
    config["_user_provider_overrides"] = {}
    user_keys: set[str] = set()

    # Try loading config file
    if config_path:
        path = Path(config_path)
        if not path.is_absolute():
            # Check in project .aidlc/ dir first, then package configs/
            if project_root:
                candidate = Path(project_root) / ".aidlc" / config_path
                if candidate.exists():
                    path = candidate
            if not path.is_absolute():
                candidate = CONFIGS_DIR / config_path
                if candidate.exists():
                    path = candidate

        if path.exists():
            with open(path) as f:
                user_config = json.load(f)
            _merge_user_config(config, user_config)
            user_keys.update(user_config.keys())
        else:
            raise FileNotFoundError(f"Config not found: {path}")
    else:
        # Check for .aidlc/config.json in project root
        if project_root:
            project_config = Path(project_root) / ".aidlc" / "config.json"
            if project_config.exists():
                with open(project_config) as f:
                    user_config = json.load(f)
                _merge_user_config(config, user_config)
                user_keys.update(user_config.keys())

    # Resolve project root
    if project_root:
        config["_project_root"] = str(Path(project_root).resolve())
    else:
        config["_project_root"] = str(Path.cwd().resolve())

    # Set up AIDLC working directory inside the project
    aidlc_dir = Path(config["_project_root"]) / ".aidlc"
    config["_aidlc_dir"] = str(aidlc_dir)
    config["_runs_dir"] = str(aidlc_dir / "runs")
    config["_reports_dir"] = str(aidlc_dir / "reports")
    config["_issues_dir"] = str(aidlc_dir / "issues")

    # Production profile defaults tighten guardrails unless user explicitly overrides.
    if config.get("runtime_profile") == "production":
        production_defaults = {
            "strict_validation": True,
            "validation_allow_no_tests": False,
            "fail_on_validation_incomplete": True,
            "fail_on_final_test_failure": True,
            "strict_change_detection": True,
            "claude_hard_timeout_seconds": 1800,
        }
        for key, value in production_defaults.items():
            if key not in user_keys:
                config[key] = value

    return config


def get_run_dir(config: dict, run_id: str) -> Path:
    run_dir = Path(config["_runs_dir"]) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def get_reports_dir(config: dict, run_id: str) -> Path:
    report_dir = Path(config["_reports_dir"]) / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def get_issues_dir(config: dict) -> Path:
    issues_dir = Path(config["_issues_dir"])
    issues_dir.mkdir(parents=True, exist_ok=True)
    return issues_dir
