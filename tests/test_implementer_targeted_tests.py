"""Tests for aidlc.implementer_targeted_tests."""

from aidlc.implementer_targeted_tests import (
    build_automatic_targeted_command,
    collect_gut_paths_from_changes,
    effective_implementation_test_command,
    expand_same_directory_gut_tests,
    strip_gtest_argument,
)


def test_collect_gut_paths_res_prefix_and_relative(tmp_path):
    gut = tmp_path / "tests" / "gut"
    gut.mkdir(parents=True)
    (gut / "test_a.gd").write_text("")
    paths = collect_gut_paths_from_changes(
        tmp_path,
        ["res://tests/gut/test_a.gd", "tests/gut/test_a.gd", "src/foo.gd"],
    )
    assert set(paths) == {"res://tests/gut/test_a.gd"}


def test_expand_same_directory_includes_siblings(tmp_path):
    gut = tmp_path / "tests" / "gut"
    gut.mkdir(parents=True)
    (gut / "test_a.gd").write_text("")
    (gut / "test_b.gd").write_text("")
    expanded = expand_same_directory_gut_tests(
        tmp_path,
        ["res://tests/gut/test_a.gd"],
    )
    assert set(expanded) == {"res://tests/gut/test_a.gd", "res://tests/gut/test_b.gd"}


def test_strip_gtest_argument():
    assert (
        strip_gtest_argument("godot --headless -s addons/gut/gut_cmdln.gd -gtest=res://x.gd")
        == "godot --headless -s addons/gut/gut_cmdln.gd"
    )


def test_build_automatic_targeted_command_appends_gtest(tmp_path):
    gut = tmp_path / "tests" / "gut"
    gut.mkdir(parents=True)
    (gut / "test_issue.gd").write_text("")
    base = "godot --headless -s addons/gut/gut_cmdln.gd -gdir=res://tests/gut"
    cmd = build_automatic_targeted_command(
        tmp_path,
        base,
        ["tests/gut/test_issue.gd"],
    )
    assert cmd is not None
    assert "-gtest=res://tests/gut/test_issue.gd" in cmd
    assert "gut_cmdln" in cmd.lower()


def test_build_automatic_targeted_command_returns_none_for_pytest(tmp_path):
    assert (
        build_automatic_targeted_command(
            tmp_path,
            "python -m pytest -q",
            ["tests/gut/test_issue.gd"],
        )
        is None
    )


def test_effective_implementation_test_command_respects_unstable_flag(tmp_path):
    base = "godot --headless -s addons/gut/gut_cmdln.gd"
    cfg = {"implementation_use_targeted_tests_when_suite_unstable": True}
    gut = tmp_path / "tests" / "gut"
    gut.mkdir(parents=True)
    (gut / "test_x.gd").write_text("")
    assert (
        effective_implementation_test_command(
            tmp_path,
            base,
            ["tests/gut/test_x.gd"],
            project_wide_tests_unstable=False,
            config=cfg,
        )
        == base
    )
    targeted = effective_implementation_test_command(
        tmp_path,
        base,
        ["tests/gut/test_x.gd"],
        project_wide_tests_unstable=True,
        config=cfg,
    )
    assert targeted != base
    assert "-gtest=" in targeted


def test_effective_implementation_test_command_template(tmp_path):
    base = "godot -s addons/gut/gut_cmdln.gd"
    gut = tmp_path / "tests" / "gut"
    gut.mkdir(parents=True)
    (gut / "test_x.gd").write_text("")
    cfg = {
        "implementation_use_targeted_tests_when_suite_unstable": True,
        "implementation_targeted_test_command": "echo {gtest_paths}",
    }
    out = effective_implementation_test_command(
        tmp_path,
        base,
        ["tests/gut/test_x.gd"],
        project_wide_tests_unstable=True,
        config=cfg,
    )
    assert out == "echo res://tests/gut/test_x.gd"


def test_sibling_expansion_capped_to_avoid_running_whole_suite(tmp_path):
    """Regression: in flat test directories the sibling expansion used to
    produce the entire suite (10+ paths), which then timed out at the
    test_timeout_seconds gate. Above the cap we drop the expansion and
    run only the explicitly-changed test files."""
    gut = tmp_path / "tests" / "gut"
    gut.mkdir(parents=True)
    # 12 sibling test files, one of which the issue actually changed.
    for i in range(12):
        (gut / f"test_feature_{i}.gd").write_text("")
    expanded = expand_same_directory_gut_tests(
        tmp_path,
        ["res://tests/gut/test_feature_0.gd"],
        cap=8,
    )
    # Cap is 8; 12 + 1 explicit > 8, so expansion is skipped and the
    # explicitly-changed path is the only thing returned.
    assert expanded == ["res://tests/gut/test_feature_0.gd"]


def test_sibling_expansion_keeps_small_dirs_intact(tmp_path):
    """When the resulting set fits under the cap, the expansion still
    runs — preserving the original "lightweight deps" intent for small
    test directories."""
    gut = tmp_path / "tests" / "gut" / "feature_a"
    gut.mkdir(parents=True)
    (gut / "test_x.gd").write_text("")
    (gut / "test_y.gd").write_text("")
    (gut / "test_z.gd").write_text("")
    expanded = expand_same_directory_gut_tests(
        tmp_path,
        ["res://tests/gut/feature_a/test_x.gd"],
        cap=8,
    )
    assert set(expanded) == {
        "res://tests/gut/feature_a/test_x.gd",
        "res://tests/gut/feature_a/test_y.gd",
        "res://tests/gut/feature_a/test_z.gd",
    }


def test_config_cap_threads_through_effective_command(tmp_path):
    """``implementation_targeted_test_sibling_expansion_cap`` from config
    is honored by the effective-command builder."""
    gut = tmp_path / "tests" / "gut"
    gut.mkdir(parents=True)
    for i in range(10):
        (gut / f"test_t_{i}.gd").write_text("")
    base = "godot --headless -s addons/gut/gut_cmdln.gd"
    cfg = {
        "implementation_use_targeted_tests_when_suite_unstable": True,
        "implementation_targeted_test_sibling_expansion_cap": 4,
    }
    out = effective_implementation_test_command(
        tmp_path,
        base,
        ["tests/gut/test_t_0.gd"],
        project_wide_tests_unstable=True,
        config=cfg,
    )
    # Cap=4; 10 siblings would balloon, so only the explicit file is in the gtest list.
    assert out.count("res://") == 1
    assert "-gtest=res://tests/gut/test_t_0.gd" in out


def test_effective_implementation_test_command_opt_out(tmp_path):
    base = "godot -s addons/gut/gut_cmdln.gd"
    cfg = {"implementation_use_targeted_tests_when_suite_unstable": False}
    gut = tmp_path / "tests" / "gut"
    gut.mkdir(parents=True)
    (gut / "test_x.gd").write_text("")
    assert (
        effective_implementation_test_command(
            tmp_path,
            base,
            ["tests/gut/test_x.gd"],
            project_wide_tests_unstable=True,
            config=cfg,
        )
        == base
    )
