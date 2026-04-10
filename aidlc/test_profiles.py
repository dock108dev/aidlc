"""Stack-specific test profile registry for AIDLC validation.

Maps project types to tiered test commands: unit → integration → E2E → build.
Users can override any command via .aidlc/config.json.
"""

import logging
from pathlib import Path


# Test command tiers — run in order, stop on first tier failure (unless progressive)
# Each entry: (detection_files, command, description)
TEST_PROFILES = {
    "python": {
        "unit": "python -m pytest -x -q --tb=short",
        "integration": "python -m pytest -x -q -m integration --tb=short",
        "e2e": "python -m pytest -x -q -m e2e --tb=short",
        "build": None,
    },
    "javascript": {
        "unit": "npm test",
        "integration": "npm run test:integration",
        "e2e": None,  # Detected separately
        "build": "npm run build",
    },
    "typescript": {
        "unit": "npm test",
        "integration": "npm run test:integration",
        "e2e": None,
        "build": "npm run build",
    },
    "rust": {
        "unit": "cargo test --lib",
        "integration": "cargo test --test '*'",
        "e2e": None,
        "build": "cargo build",
    },
    "go": {
        "unit": "go test ./... -short",
        "integration": "go test ./... -run Integration",
        "e2e": "go test ./... -tags=e2e",
        "build": "go build ./...",
    },
    "ruby": {
        "unit": "bundle exec rspec --fail-fast",
        "integration": "bundle exec rspec --tag integration",
        "e2e": None,
        "build": None,
    },
    "java": {
        "unit": "mvn test -q",
        "integration": "mvn verify -q",
        "e2e": None,
        "build": "mvn package -q -DskipTests",
    },
    "godot": {
        "unit": "godot --headless --script res://addons/gut/gut_cmdln.gd",
        "integration": None,
        "e2e": "godot --headless --script res://tests/run_tests.gd",
        "build": "godot --headless --export-release",
    },
    "unity": {
        "unit": None,  # Requires Unity CLI which varies
        "integration": None,
        "e2e": None,
        "build": None,
    },
    "swift": {
        "unit": "swift test",
        "integration": None,
        "e2e": None,
        "build": "swift build",
    },
    "c_cpp": {
        "unit": "make test",
        "integration": None,
        "e2e": None,
        "build": "make",
    },
}

# E2E framework detection — checked separately from main project type
E2E_FRAMEWORKS = {
    "playwright": {
        "detect": ["playwright.config.js", "playwright.config.ts"],
        "command": "npx playwright test --reporter=list",
    },
    "cypress": {
        "detect": ["cypress.config.js", "cypress.config.ts", "cypress.json"],
        "command": "npx cypress run",
    },
    "gut": {
        "detect": ["addons/gut"],
        "command": "godot --headless --script res://addons/gut/gut_cmdln.gd",
    },
}

# Map scanner project types to profile keys
PROJECT_TYPE_MAP = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "javascript/typescript": "javascript",
    "rust": "rust",
    "go": "go",
    "ruby": "ruby",
    "java": "java",
    "java/kotlin": "java",
    "swift": "swift",
    "c/c++": "c_cpp",
    "make": "c_cpp",
}


def detect_test_profile(project_root: Path, project_type: str, config: dict) -> dict:
    """Detect the appropriate test profile for this project.

    Returns a dict with keys: unit, integration, e2e, build — each a command string or None.
    User config overrides take precedence.
    """
    profile = {"unit": None, "integration": None, "e2e": None, "build": None}

    # Godot detection (not in scanner's PROJECT_INDICATORS)
    if (project_root / "project.godot").exists():
        base = TEST_PROFILES.get("godot", {})
        profile.update({k: v for k, v in base.items() if v})
    else:
        # Match from project type string
        for type_key, profile_key in PROJECT_TYPE_MAP.items():
            if type_key in project_type.lower():
                base = TEST_PROFILES.get(profile_key, {})
                profile.update({k: v for k, v in base.items() if v})
                break

    # E2E framework detection
    for fw_name, fw_config in E2E_FRAMEWORKS.items():
        for detect_path in fw_config["detect"]:
            if (project_root / detect_path).exists():
                profile["e2e"] = fw_config["command"]
                break

    # Check for common test script patterns in package.json
    pkg_json = project_root / "package.json"
    if pkg_json.exists():
        try:
            import json
            data = json.loads(pkg_json.read_text(errors="replace"))
            scripts = data.get("scripts", {})
            if "test:unit" in scripts and not profile["unit"]:
                profile["unit"] = "npm run test:unit"
            if "test:integration" in scripts and not profile["integration"]:
                profile["integration"] = "npm run test:integration"
            if "test:e2e" in scripts and not profile["e2e"]:
                profile["e2e"] = "npm run test:e2e"
            if "test:uat" in scripts and not profile["e2e"]:
                profile["e2e"] = "npm run test:uat"
        except (OSError, json.JSONDecodeError):
            logging.getLogger("aidlc").warning(
                "Unable to parse package.json while detecting test profile scripts."
            )

    # User config overrides
    profile["unit"] = config.get("run_tests_command", profile["unit"])
    profile["e2e"] = config.get("e2e_test_command", profile["e2e"])
    profile["build"] = config.get("build_validation_command", profile["build"])

    return profile
