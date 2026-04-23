"""ISSUE-008: ``aidlc reset`` clears stale state without nuking config or auth.

Default deletes runs/, reports/, issues/, session/, and run artifacts; preserves
config.json. Flags: --all (also config), --keep-issues, --dry-run, --yes/-y.
"""

from __future__ import annotations

import argparse

from aidlc.cli_commands import _reset_targets, cmd_reset


def _populate(tmp_path):
    """Create a realistic .aidlc/ tree to delete."""
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir(parents=True, exist_ok=True)
    (aidlc / "runs" / "aidlc_2026").mkdir(parents=True)
    (aidlc / "runs" / "aidlc_2026" / "state.json").write_text("{}")
    (aidlc / "reports" / "aidlc_2026").mkdir(parents=True)
    (aidlc / "issues").mkdir()
    (aidlc / "issues" / "ISSUE-001.md").write_text("# ISSUE-001")
    (aidlc / "session" / "20260422").mkdir(parents=True)
    (aidlc / "config.json").write_text('{"providers": {}}')
    (aidlc / "audit_result.json").write_text("{}")
    (aidlc / "planning_index.md").write_text("# Index")
    return aidlc


def _args(project, **kw):
    """Helper to build argparse.Namespace stand-in for cmd_reset."""
    return argparse.Namespace(
        project=str(project),
        reset_all=kw.get("reset_all", False),
        keep_issues=kw.get("keep_issues", False),
        dry_run=kw.get("dry_run", False),
        yes=kw.get("yes", True),  # default to non-interactive in tests
    )


def test_reset_targets_default_excludes_config(tmp_path):
    aidlc = tmp_path / ".aidlc"
    targets = _reset_targets(aidlc, keep_issues=False, reset_all=False)
    target_names = {p.name for p in targets}
    # Default targets:
    for expected in ("runs", "reports", "session", "issues", "audit_result.json"):
        assert expected in target_names
    # config.json is preserved unless --all:
    assert "config.json" not in target_names


def test_reset_targets_keep_issues(tmp_path):
    aidlc = tmp_path / ".aidlc"
    targets = _reset_targets(aidlc, keep_issues=True, reset_all=False)
    assert (aidlc / "issues") not in targets


def test_reset_targets_all_includes_config(tmp_path):
    aidlc = tmp_path / ".aidlc"
    targets = _reset_targets(aidlc, keep_issues=False, reset_all=True)
    assert (aidlc / "config.json") in targets


def test_cmd_reset_default_preserves_config(tmp_path, capsys):
    aidlc = _populate(tmp_path)
    cmd_reset(_args(tmp_path), version="test")
    # config.json survives the default reset.
    assert (aidlc / "config.json").exists()
    # Everything else is gone.
    assert not (aidlc / "runs").exists()
    assert not (aidlc / "issues").exists()
    assert not (aidlc / "session").exists()
    assert not (aidlc / "audit_result.json").exists()


def test_cmd_reset_keep_issues(tmp_path):
    aidlc = _populate(tmp_path)
    cmd_reset(_args(tmp_path, keep_issues=True), version="test")
    assert (aidlc / "issues" / "ISSUE-001.md").exists()
    assert (aidlc / "config.json").exists()
    assert not (aidlc / "runs").exists()


def test_cmd_reset_all_deletes_config(tmp_path):
    aidlc = _populate(tmp_path)
    cmd_reset(_args(tmp_path, reset_all=True), version="test")
    assert not (aidlc / "config.json").exists()
    assert not (aidlc / "runs").exists()


def test_cmd_reset_dry_run_changes_nothing(tmp_path):
    aidlc = _populate(tmp_path)
    cmd_reset(_args(tmp_path, dry_run=True), version="test")
    # Everything still present.
    assert (aidlc / "runs" / "aidlc_2026" / "state.json").exists()
    assert (aidlc / "issues" / "ISSUE-001.md").exists()
    assert (aidlc / "config.json").exists()


def test_cmd_reset_no_aidlc_dir(tmp_path, capsys):
    cmd_reset(_args(tmp_path), version="test")
    captured = capsys.readouterr().out
    assert "No .aidlc/" in captured


def test_cmd_reset_already_clean(tmp_path, capsys):
    """When .aidlc/ exists but contains nothing matching the targets."""
    (tmp_path / ".aidlc").mkdir()
    (tmp_path / ".aidlc" / "config.json").write_text("{}")
    cmd_reset(_args(tmp_path), version="test")
    captured = capsys.readouterr().out
    assert "Already clean" in captured
    assert (tmp_path / ".aidlc" / "config.json").exists()


def test_cmd_reset_confirmation_prompt_aborts(tmp_path, monkeypatch, capsys):
    aidlc = _populate(tmp_path)
    # User says "no" to the confirmation prompt.
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    args = _args(tmp_path, yes=False)
    cmd_reset(args, version="test")
    captured = capsys.readouterr().out
    assert "Aborted" in captured
    # Nothing got deleted.
    assert (aidlc / "runs" / "aidlc_2026" / "state.json").exists()


def test_cmd_reset_confirmation_prompt_proceeds(tmp_path, monkeypatch):
    aidlc = _populate(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    args = _args(tmp_path, yes=False)
    cmd_reset(args, version="test")
    assert not (aidlc / "runs").exists()
    assert (aidlc / "config.json").exists()
