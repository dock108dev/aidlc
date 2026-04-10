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
    "claude_model_implementation": "opus",   # model for implementation
    "claude_model_finalization": "sonnet",    # model for finalization passes
    "claude_long_run_warn_seconds": 300,    # warn every N seconds if Claude is still running
    "claude_hard_timeout_seconds": 0,       # 0 = disabled (no hard kill)
    "retry_max_attempts": 2,
    "retry_base_delay_seconds": 30,
    "retry_max_delay_seconds": 300,
    "retry_backoff_factor": 2.0,
    "max_consecutive_failures": 3,
    "diminishing_returns_window": 5,       # track last N cycles for diminishing returns
    "diminishing_returns_threshold": 2,    # exit after N consecutive cycles with no new issues
    "finalization_budget_percent": 10,
    "max_implementation_attempts": 3,
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

    # Research settings
    "research_max_scope_files": 10,         # max files to read per research action
    "research_max_source_chars": 15000,     # max chars per scope file
    "research_max_per_cycle": 2,            # max research actions per planning cycle
    "research_timeout_seconds": 900,        # 15 min timeout per research call

    # Doc-gap detection
    "doc_gap_detection_enabled": True,
    "doc_gap_max_items": 50,                # cap gaps passed to planner prompt

    # Context preparation
    "project_brief_max_chars": 20000,       # max size of generated project brief
    "phase_context_max_chars": 20000,       # max chars for phase-focused docs per cycle
    "max_planning_prompt_chars": 60000,     # total prompt budget per planning cycle

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
            "claude_hard_timeout_seconds": 3600,
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
