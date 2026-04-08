"""Config loader for AIDLC runner. Project-agnostic."""

import json
import os
from pathlib import Path

# Framework root (where aidlc package lives)
AIDLC_PKG_ROOT = Path(__file__).parent
CONFIGS_DIR = AIDLC_PKG_ROOT / "configs"

# Default config
DEFAULT_CONFIG = "default.json"

# Required config keys
REQUIRED_KEYS = [
    "plan_budget_hours",
]

# Default configuration values
DEFAULTS = {
    "plan_budget_hours": 4,
    "checkpoint_interval_minutes": 15,
    "dry_run": False,
    "claude_cli_command": "claude",
    "claude_model": "opus",
    "claude_timeout_seconds": 600,
    "retry_max_attempts": 2,
    "retry_base_delay_seconds": 30,
    "retry_max_delay_seconds": 300,
    "retry_backoff_factor": 2.0,
    "max_consecutive_failures": 3,
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
}


def load_config(config_path: str | None = None, project_root: str | None = None) -> dict:
    """Load and validate config. Merges defaults with user config."""
    config = dict(DEFAULTS)

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
