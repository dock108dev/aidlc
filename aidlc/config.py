"""Config loader for AIDLC runner. Project-agnostic."""

import json
from pathlib import Path

# Framework root (where aidlc package lives)
AIDLC_PKG_ROOT = Path(__file__).parent
CONFIGS_DIR = AIDLC_PKG_ROOT / "configs"

# Default configuration values
DEFAULTS = {
    "runtime_profile": "standard",          # standard | production
    "plan_budget_hours": 4,
    "checkpoint_interval_minutes": 15,
    "dry_run": False,
    "claude_cli_command": "claude",
    "claude_model": "opus",                  # default model (used for implementation)
    "claude_model_planning": "sonnet",       # model for planning cycles
    "claude_model_research": "sonnet",       # model for research actions
    "claude_model_implementation": "sonnet",   # default model for implementation
    "claude_model_implementation_complex": "opus",  # model for complex implementation issues
    "claude_model_finalization": "sonnet",    # model for finalization passes
    "claude_long_run_warn_seconds": 300,    # warn every N seconds if Claude is still running
    "claude_hard_timeout_seconds": 1800,    # default 30-minute escape hatch for stuck runs
    "claude_timeout_grace_seconds": 30,     # wait for graceful Claude shutdown before force-kill
    # Telemetry/cost tracking:
    # - auto: use exact CLI-reported cost when available, otherwise estimate from token rates
    # - exact_only: track only exact cost values from CLI metadata
    # - estimate_only: always estimate from token rates
    "telemetry_cost_mode": "auto",
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
    },
    "retry_max_attempts": 2,
    "retry_base_delay_seconds": 30,
    "retry_max_delay_seconds": 300,
    "retry_backoff_factor": 2.0,
    "claude_service_outage_max_wait_seconds": 7200,  # keep retrying on 5xx/outage for up to 2h
    "max_consecutive_failures": 3,
    "diminishing_returns_window": 5,       # track last N cycles for diminishing returns
    "diminishing_returns_threshold": 2,    # exit after N consecutive cycles with no new issues
    "finalization_budget_percent": 10,
    "planning_finalization_grace_cycles": 1,  # finalization cycles allowed after budget exhaustion
    "max_implementation_attempts": 3,
    "implementation_escalate_on_retry": True,  # escalate retries to complex implementation model
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
    "max_planning_cycles": 0,       # 0 = unlimited (dry-run defaults to 3)
    "max_implementation_cycles": 0,  # 0 = unlimited (dry-run defaults to 3)
    "run_tests_command": None,       # auto-detected if not set
    "test_timeout_seconds": 300,
    "max_doc_chars": 10000,
    "max_context_chars": 80000,
    "max_implementation_context_chars": 30000,
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
    "audit_depth": "quick",                 # default depth when --audit is used
    "audit_max_claude_calls": 10,           # cap Claude calls during full audit
    "audit_max_source_chars_per_module": 15000,  # source chars sent to Claude per module
    "audit_source_extensions": [
        ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
    ],
    "audit_exclude_patterns": [
        "**/test*/**", "**/vendor/**", "**/node_modules/**", "**/.git/**",
    ],
    "audit_runtime_enabled": True,          # run build/unit/integration/e2e checks during full audit
    "audit_runtime_timeout_seconds": 600,   # timeout for each runtime audit command
    "audit_coverage_threshold_percent": 85,  # focus shifts to UI when >= threshold
    "audit_playwright_headless": True,      # enforce headless Playwright in runtime audit
    "audit_playwright_command_override": None,  # optional custom Playwright command
    "audit_braindump_enabled": True,        # generate BRAINDUMP.md during full audit
    "audit_braindump_path": "BRAINDUMP.md",
    "audit_planning_workload_stop_ratio": 0.95,  # stop adding issue seeds near planning budget
    "audit_research_estimate_default_hours": 2.0,
    "audit_issue_estimate_defaults": {      # default projected effort per issue priority
        "high": 3.0,
        "medium": 1.5,
        "low": 0.75,
    },
    "audit_include_deferred_backlog": True,  # include overflow ideas after workload cap

    # Research settings
    "research_max_scope_files": 10,         # max files to read per research action
    "research_max_source_chars": 15000,     # max chars per scope file
    "research_max_per_cycle": 2,            # max research actions per planning cycle
    "research_timeout_seconds": 900,        # 15 min timeout per research call

    # Doc-gap detection
    "doc_gap_detection_enabled": True,
    "doc_gap_max_items": 50,                # cap gaps passed to planner prompt
    "planning_doc_min_chars": 800,          # minimum chars for "sufficient" planning docs

    # Context preparation
    "project_brief_max_chars": 20000,       # max size of generated project brief
    "phase_context_max_chars": 20000,       # max chars for phase-focused docs per cycle
    "max_planning_prompt_chars": 60000,     # total prompt budget per planning cycle
    "planning_issue_index_max_items": 40,   # max issues listed inline in planning prompt
    "planning_issue_index_include_all_until": 30,  # list all issues until this count
    "planning_last_cycle_notes_max_chars": 500,    # max chars from previous-cycle notes

    # Validation loop
    "validation_enabled": True,
    "strict_validation": False,             # if True, validation failures pause run
    "validation_allow_no_tests": True,      # if False, missing tests fail validation
    "fail_on_validation_incomplete": False,  # if True, incomplete validation pauses run
    "validation_max_cycles": 3,             # max test-fix iterations
    "validation_batch_size": 10,            # max fix issues per cycle
    "test_profile_mode": "progressive",     # unit → integration → e2e (stop on first fail)
    "e2e_test_command": None,               # override E2E test command
    "build_validation_command": None,       # override build command

    # Finalization
    "finalize_enabled": True,               # master switch for finalization
    "fail_on_final_test_failure": False,    # if True, failed final suite pauses run
    "strict_change_detection": False,       # if True, impl success requires verifiable changes
    "finalize_passes": None,                # None = all; or ["ssot", "docs"]
    "finalize_timeout_seconds": 900,        # 15 min per pass
    "planning_action_failure_ratio_threshold": 0.6,  # fail cycle if too many actions fail
}


def load_config(config_path: str | None = None, project_root: str | None = None) -> dict:
    """Load and validate config. Merges defaults with user config."""
    config = dict(DEFAULTS)
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
            config.update(user_config)
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
                config.update(user_config)
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
