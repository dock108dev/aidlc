"""Auto-detect project configuration for AIDLC.

Inspects the project to determine: test commands, build commands,
project type, framework-specific settings, and recommended config values.
Used during `aidlc init` and after finalization to keep config current.
"""

import json
import time
from pathlib import Path

from .scanner import PROJECT_INDICATORS
from .test_profiles import detect_test_profile


def detect_config(project_root: Path) -> dict:
    """Auto-detect the best config for this project.

    Returns a dict of config values to merge into .aidlc/config.json.
    Only includes values that were actually detected — doesn't override
    existing user config.
    """
    detected = {}

    # Detect project type
    project_types = []
    for filename, ptype in PROJECT_INDICATORS.items():
        if (project_root / filename).exists():
            project_types.append(ptype)
    # Godot detection (not in PROJECT_INDICATORS)
    if (project_root / "project.godot").exists():
        project_types.append("godot")
    # Unity detection
    if (project_root / "Assets").is_dir() and (project_root / "ProjectSettings").is_dir():
        project_types.append("unity")

    project_type = ", ".join(sorted(set(project_types))) if project_types else "unknown"
    detected["_detected_project_type"] = project_type

    # Detect test profile
    test_profile = detect_test_profile(project_root, project_type, {})

    if test_profile["unit"]:
        detected["run_tests_command"] = test_profile["unit"]
    if test_profile["e2e"]:
        detected["e2e_test_command"] = test_profile["e2e"]
    if test_profile["build"]:
        detected["build_validation_command"] = test_profile["build"]

    # Stack-specific config adjustments
    if "godot" in project_type:
        detected["claude_hard_timeout_seconds"] = 900  # Godot scenes take longer
        detected["max_implementation_context_chars"] = 40000
    elif "rust" in project_type:
        detected["test_timeout_seconds"] = 600  # Rust compiles slow
    elif "unity" in project_type:
        detected["claude_hard_timeout_seconds"] = 900

    # Detect lint/format commands
    lint_cmd = _detect_lint_command(project_root, project_type)
    if lint_cmd:
        detected["lint_command"] = lint_cmd

    # Detect package manager
    if (project_root / "pnpm-lock.yaml").exists():
        # Adjust npm commands to pnpm
        for key in (
            "run_tests_command",
            "e2e_test_command",
            "build_validation_command",
        ):
            if key in detected and detected[key] and detected[key].startswith("npm"):
                detected[key] = detected[key].replace("npm", "pnpm", 1)
    elif (project_root / "yarn.lock").exists():
        for key in (
            "run_tests_command",
            "e2e_test_command",
            "build_validation_command",
        ):
            if key in detected and detected[key] and detected[key].startswith("npm"):
                detected[key] = detected[key].replace("npm", "yarn", 1)

    return detected


def update_config_file(project_root: Path, detected: dict, logger=None) -> dict:
    """Merge detected config into .aidlc/config.json.

    Only adds keys that don't already exist (user overrides preserved).
    Returns the final merged config.
    """
    config_path = project_root / ".aidlc" / "config.json"

    existing = {}
    if config_path.exists():
        try:
            raw = config_path.read_text()
            existing = json.loads(raw)
        except OSError as exc:
            if logger:
                logger.error(f"Failed reading config for auto-detect merge: {exc}")
            raise
        except json.JSONDecodeError as exc:
            backup_path = config_path.with_suffix(f".corrupt-{int(time.time())}.json.bak")
            try:
                backup_path.write_text(raw if "raw" in locals() else "")
            except OSError:
                backup_path = None
            message = (
                f"Config file is not valid JSON: {config_path}. "
                "Refusing to auto-merge detected values."
            )
            if backup_path:
                message += f" Corrupt content backed up to {backup_path}."
            if logger:
                logger.error(message)
            raise ValueError(message) from exc

    # Only add detected values for keys not already set by user
    updated = False
    for key, value in detected.items():
        if key.startswith("_"):
            continue  # Skip internal keys
        if key not in existing or existing[key] is None:
            existing[key] = value
            updated = True
            if logger:
                logger.info(f"  Auto-detected {key}: {value}")

    if updated:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(existing, f, indent=2)

    return existing


def describe_detected(detected: dict) -> list[str]:
    """Return human-readable lines describing what was detected."""
    lines = []
    ptype = detected.get("_detected_project_type", "unknown")
    lines.append(f"Project type: {ptype}")

    if detected.get("run_tests_command"):
        lines.append(f"Test command: {detected['run_tests_command']}")
    if detected.get("e2e_test_command"):
        lines.append(f"E2E tests: {detected['e2e_test_command']}")
    if detected.get("build_validation_command"):
        lines.append(f"Build command: {detected['build_validation_command']}")
    if detected.get("lint_command"):
        lines.append(f"Lint command: {detected['lint_command']}")

    return lines


def _detect_lint_command(project_root: Path, project_type: str) -> str | None:
    """Detect lint/format command for the project."""
    # JS/TS
    pkg_json = project_root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(errors="replace"))
            scripts = data.get("scripts", {})
            if "lint" in scripts:
                return "npm run lint"
            if "lint:fix" in scripts:
                return "npm run lint:fix"
        except (OSError, json.JSONDecodeError):
            import logging

            logging.getLogger("aidlc").warning(
                "Unable to parse package.json while detecting lint command."
            )

    # Python
    if "python" in project_type:
        if (project_root / "pyproject.toml").exists():
            try:
                content = (project_root / "pyproject.toml").read_text(errors="replace")
                if "[tool.ruff]" in content:
                    return "ruff check ."
                if "[tool.flake8]" in content or (project_root / ".flake8").exists():
                    return "flake8 ."
            except OSError:
                import logging

                logging.getLogger("aidlc").warning(
                    "Unable to read pyproject.toml while detecting lint command."
                )

    # Rust
    if "rust" in project_type:
        return "cargo clippy"

    # Go (but not godot)
    if "go" in project_type and "godot" not in project_type:
        return "golangci-lint run"

    # Godot
    if "godot" in project_type:
        if (project_root / ".gdlintrc").exists() or (project_root / "gdlint.cfg").exists():
            return "gdlint ."

    return None
