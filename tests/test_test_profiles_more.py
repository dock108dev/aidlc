"""Extra coverage for aidlc.test_profiles.detect_test_profile."""

import json

from aidlc.test_profiles import detect_test_profile


def test_godot_project_uses_godot_profile(tmp_path):
    (tmp_path / "project.godot").write_text("[application]\n")
    p = detect_test_profile(tmp_path, "anything", {})
    assert p.get("unit") and "godot" in p["unit"].lower()


def test_playwright_config_sets_e2e(tmp_path):
    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    p = detect_test_profile(tmp_path, "typescript", {})
    assert p.get("e2e") and "playwright" in p["e2e"].lower()


def test_package_json_test_unit_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test:unit": "jest"}})
    )
    p = detect_test_profile(tmp_path, "unknown-stack", {})
    assert p["unit"] == "npm run test:unit"


def test_package_json_test_integration_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test:integration": "jest -i"}})
    )
    p = detect_test_profile(tmp_path, "unknown-stack", {})
    assert p["integration"] == "npm run test:integration"


def test_package_json_test_e2e_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test:e2e": "wdio"}})
    )
    p = detect_test_profile(tmp_path, "unknown-stack", {})
    assert p["e2e"] == "npm run test:e2e"


def test_package_json_test_uat_used_when_no_e2e(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test:uat": "uat"}}))
    p = detect_test_profile(tmp_path, "unknown-stack", {})
    assert p["e2e"] == "npm run test:uat"


def test_package_json_invalid_logs_warning(tmp_path, caplog):
    (tmp_path / "package.json").write_text("{")
    caplog.set_level("WARNING")
    detect_test_profile(tmp_path, "javascript", {})
    assert "Unable to parse package.json" in caplog.text
