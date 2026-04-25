"""Coverage for aidlc.__main__ entry helpers and main() dispatch."""

import sys
from unittest.mock import MagicMock, patch

import aidlc.__main__ as main_mod
import pytest
from aidlc.__main__ import main, parse_budget


@pytest.mark.parametrize(
    "wrapper,inner",
    [
        # The audit/improve/plan/finalize/validate wrappers were removed in
        # the core-focus audit (audit + finalize run inside ``aidlc run``;
        # the others were duplicating the lifecycle).
        ("cmd_precheck", "_cmd_precheck"),
        ("cmd_init", "_cmd_init"),
        ("cmd_status", "_cmd_status"),
        ("cmd_accounts", "_cmd_accounts"),
        ("cmd_provider", "_cmd_provider"),
        ("cmd_usage", "_cmd_usage"),
        ("cmd_config_show", "_cmd_config_show"),
    ],
)
def test_thin_command_wrappers_delegate(wrapper, inner):
    args = MagicMock()
    with patch.object(main_mod, inner) as mock_inner:
        getattr(main_mod, wrapper)(args)
    mock_inner.assert_called_once_with(args, main_mod.__version__)


def test_parse_budget_hours_minutes_and_plain():
    assert parse_budget(" 4H ") == 4.0
    assert parse_budget("30M") == 0.5
    assert parse_budget("2.25") == 2.25


@patch("aidlc.__main__._cmd_precheck")
def test_main_dispatches_precheck(mock_precheck):
    with patch.object(sys, "argv", ["aidlc", "precheck", "--project", "/tmp"]):
        main()
    mock_precheck.assert_called_once()


@patch("aidlc.__main__._cmd_init")
def test_main_dispatches_init(mock_init):
    with patch.object(sys, "argv", ["aidlc", "init", "--project", "/tmp"]):
        main()
    mock_init.assert_called_once()


@patch("aidlc.__main__.cmd_run")
def test_main_dispatches_run(mock_cmd_run):
    with patch.object(sys, "argv", ["aidlc", "run", "--project", "/tmp", "--dry-run"]):
        main()
    mock_cmd_run.assert_called_once()


@patch("aidlc.__main__._cmd_config_show")
def test_main_dispatches_config(mock_cfg):
    with patch.object(sys, "argv", ["aidlc", "config", "show", "--project", "/tmp"]):
        main()
    mock_cfg.assert_called_once()


@pytest.mark.parametrize(
    "argv_patch,target",
    [
        (["aidlc", "status", "--project", "/tmp"], "aidlc.__main__.cmd_status"),
        (
            ["aidlc", "accounts", "--project", "/tmp", "list"],
            "aidlc.__main__.cmd_accounts",
        ),
        (
            ["aidlc", "provider", "--project", "/tmp", "list"],
            "aidlc.__main__.cmd_provider",
        ),
        (["aidlc", "usage", "--project", "/tmp"], "aidlc.__main__.cmd_usage"),
    ],
)
def test_main_dispatches_other_commands(argv_patch, target):
    with patch(target) as mock_cmd:
        with patch.object(sys, "argv", argv_patch):
            main()
    mock_cmd.assert_called_once()


@pytest.mark.parametrize("removed_cmd", ["audit", "improve", "plan", "finalize", "validate"])
def test_removed_commands_no_longer_parse(removed_cmd, capsys):
    """Commands removed in the core-focus audit must not silently re-parse.

    argparse should fail with SystemExit since these subparsers were dropped.
    """
    with patch.object(sys, "argv", ["aidlc", removed_cmd]):
        with pytest.raises(SystemExit):
            main()


def test_main_no_subcommand_prints_help(capsys):
    with patch.object(sys, "argv", ["aidlc"]):
        main()
    out = capsys.readouterr().out
    assert "precheck" in out or "init" in out or "run" in out


@patch("aidlc.__main__.run_full")
@patch("aidlc.__main__.load_config")
def test_cmd_run_applies_plan_budget_hours(mock_load_config, mock_run_full, tmp_path):
    mock_load_config.return_value = {
        "_project_root": str(tmp_path),
        "runtime_profile": "dev",
    }
    from aidlc.__main__ import cmd_run

    args = MagicMock()
    args.project = str(tmp_path)
    args.config = None
    args.resume = True
    args.revert_to_cycle = None
    args.verbose = False
    args.plan_budget = "90m"
    args.max_plan_cycles = None
    args.max_impl_cycles = None
    args.audit = None
    args.skip_finalize = False
    args.skip_validation = False
    args.passes = None
    args.dry_run = True
    args.plan_only = False
    args.implement_only = False
    cmd_run(args)
    merged = mock_load_config.return_value
    assert merged.get("plan_budget_hours") == pytest.approx(1.5)


@patch("aidlc.__main__.load_config")
@patch("aidlc.__main__.run_full")
def test_cmd_run_sets_finalize_passes(mock_run_full, mock_load_config, tmp_path):
    from aidlc.__main__ import cmd_run

    mock_load_config.return_value = {
        "_project_root": str(tmp_path),
        "runtime_profile": "dev",
    }
    args = MagicMock()
    args.project = str(tmp_path)
    args.config = None
    args.resume = True
    args.revert_to_cycle = None
    args.verbose = False
    args.plan_budget = None
    args.max_plan_cycles = None
    args.max_impl_cycles = None
    args.audit = None
    args.skip_finalize = False
    args.skip_validation = False
    args.passes = "docs,lint"
    args.dry_run = True
    args.plan_only = False
    args.implement_only = False
    cmd_run(args)
    mock_run_full.assert_called_once()
    call_kw = mock_run_full.call_args.kwargs
    assert call_kw["finalize_passes"] == ["docs", "lint"]


@patch("aidlc.state_manager.save_state")
@patch("aidlc.state_manager.load_cycle_snapshot")
@patch("aidlc.state_manager.list_cycle_snapshots")
@patch("aidlc.state_manager.find_latest_run")
@patch("aidlc.__main__.load_config")
def test_cmd_run_revert_cycle_restores_snapshot(
    mock_load_config,
    mock_find_run,
    mock_list_snaps,
    mock_load_snap,
    mock_save,
    tmp_path,
    capsys,
):
    from aidlc.__main__ import cmd_run
    from aidlc.models import RunPhase, RunState, RunStatus

    mock_load_config.return_value = {"_project_root": str(tmp_path)}
    run_dir = tmp_path / ".aidlc" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    mock_find_run.return_value = run_dir
    mock_list_snaps.return_value = [1, 2, 3]
    snap = RunState(run_id="rid", config_name="c")
    snap.issues_created = 4
    snap.planning_cycles = 2
    snap.phase = RunPhase.PLANNING
    mock_load_snap.return_value = snap

    args = MagicMock()
    args.project = str(tmp_path)
    args.config = None
    args.revert_to_cycle = 2
    cmd_run(args)
    mock_save.assert_called_once()
    saved = mock_save.call_args[0][0]
    assert saved.status == RunStatus.PAUSED
    assert "cycle 2" in saved.stop_reason
    assert "Reverted" in capsys.readouterr().out


@patch("aidlc.__main__.load_config")
def test_cmd_run_production_rejects_skip_flags(mock_load_config, tmp_path, capsys):
    from aidlc.__main__ import cmd_run

    mock_load_config.return_value = {
        "_project_root": str(tmp_path),
        "runtime_profile": "production",
    }
    args = MagicMock()
    args.project = str(tmp_path)
    args.config = None
    args.resume = True
    args.revert_to_cycle = None
    args.verbose = False
    args.plan_budget = None
    args.max_plan_cycles = None
    args.max_impl_cycles = None
    args.audit = None
    args.skip_finalize = False
    args.skip_validation = True
    args.passes = None
    args.dry_run = True
    args.plan_only = False
    args.implement_only = False
    with pytest.raises(SystemExit):
        cmd_run(args)


@patch("aidlc.__main__.load_config")
def test_cmd_run_production_rejects_skip_finalize(mock_load_config, tmp_path):
    from aidlc.__main__ import cmd_run

    mock_load_config.return_value = {
        "_project_root": str(tmp_path),
        "runtime_profile": "production",
    }
    args = MagicMock()
    args.project = str(tmp_path)
    args.config = None
    args.resume = True
    args.revert_to_cycle = None
    args.verbose = False
    args.plan_budget = None
    args.max_plan_cycles = None
    args.max_impl_cycles = None
    args.audit = None
    args.skip_finalize = True
    args.skip_validation = False
    args.passes = None
    args.dry_run = True
    args.plan_only = False
    args.implement_only = False
    with pytest.raises(SystemExit):
        cmd_run(args)
