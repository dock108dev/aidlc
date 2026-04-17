"""High-yield coverage for aidlc.cli_commands (mocked I/O and subprocess)."""

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest
from aidlc.audit_models import AuditConflict, AuditResult, CoverageInfo
from aidlc.cli_commands import (
    cmd_accounts,
    cmd_audit,
    cmd_finalize,
    cmd_improve,
    cmd_init,
    cmd_plan,
    cmd_precheck,
    cmd_status,
)
from aidlc.models import RunPhase, RunState, RunStatus
from aidlc.state_manager import save_state


@pytest.fixture
def version():
    return "9.9.9-test"


def _args(**kw):
    base = dict(
        project=None,
        verbose=False,
        with_docs=False,
        providers=False,
        full=False,
        config=None,
        concern=None,
        plan_only=False,
        skip_wizard=False,
        wizard_only=False,
        review=False,
        passes=None,
    )
    base.update(kw)
    return Namespace(**base)


@patch("aidlc.cli_commands.sys.exit")
@patch("aidlc.precheck.run_precheck")
@patch("aidlc.cli_commands._print_precheck")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_precheck_ready(mock_banner, mock_pp, mock_rp, mock_exit, version, tmp_path, capsys):
    ready = MagicMock()
    ready.ready = True
    mock_rp.return_value = ready
    cmd_precheck(_args(project=str(tmp_path)), version)
    mock_exit.assert_not_called()


@patch("aidlc.cli_commands.sys.exit")
@patch("aidlc.precheck.run_precheck")
@patch("aidlc.cli_commands._print_precheck")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_precheck_not_ready_exits(
    mock_banner, mock_pp, mock_rp, mock_exit, version, tmp_path, capsys
):
    ready = MagicMock()
    ready.ready = False
    mock_rp.return_value = ready
    cmd_precheck(_args(project=str(tmp_path)), version)
    mock_exit.assert_called_once_with(1)


@patch("aidlc.cli_commands._get_template_dir")
@patch("aidlc.cli_commands.write_default_config")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_init_new_project(mock_banner, mock_wdc, mock_tpl, version, tmp_path, capsys):
    mock_tpl.return_value = tmp_path / "tpl"
    mock_tpl.return_value.mkdir()
    (mock_tpl.return_value / "README.md").write_text("t")
    cmd_init(_args(project=str(tmp_path), with_docs=True), version)
    assert (tmp_path / "README.md").exists()


@patch("aidlc.cli_commands._get_template_dir")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_init_with_docs_missing_templates_exits(mock_banner, mock_tpl, version, tmp_path):
    mock_tpl.return_value = tmp_path / "missing_tpl"
    with patch("aidlc.cli_commands.sys.exit") as mock_exit:
        cmd_init(_args(project=str(tmp_path), with_docs=True), version)
    mock_exit.assert_called_once_with(1)


@patch("aidlc.cli_commands._print_banner")
def test_cmd_init_existing_aidlc_without_docs_returns(mock_banner, version, tmp_path):
    (tmp_path / ".aidlc").mkdir()
    cmd_init(_args(project=str(tmp_path), with_docs=False), version)


@patch("aidlc.cli_commands.cmd_provider_auth")
@patch("aidlc.routing.engine.ProviderRouter")
@patch("aidlc.cli_commands.load_config")
@patch("aidlc.cli_commands.run_config_wizard")
@patch("aidlc.cli_commands.write_default_config")
@patch("aidlc.config_detect.detect_config")
@patch("aidlc.config_detect.describe_detected")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_init_providers_wizard(
    mock_banner,
    mock_desc,
    mock_det,
    mock_wdc,
    mock_wiz,
    mock_load,
    mock_router_cls,
    mock_auth,
    version,
    tmp_path,
):
    mock_det.return_value = {"_detected_project_type": "python"}
    mock_desc.return_value = ["Project type: python"]

    def _fake_write_default(aidlc_dir, detected_overrides=None):
        aidlc_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("issues", "runs", "reports"):
            (aidlc_dir / sub).mkdir(exist_ok=True)
        (aidlc_dir / "config.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "claude": {
                            "enabled": True,
                            "cli_command": "claude",
                            "default_model": "sonnet",
                        }
                    },
                    "routing_strategy": "balanced",
                    "plan_budget_hours": 4,
                }
            )
        )

    mock_wdc.side_effect = _fake_write_default

    adapter = MagicMock()
    h = MagicMock()
    h.is_usable = False
    h.status = MagicMock()
    h.status.value = "bad"
    adapter.validate_health.return_value = h
    router = MagicMock()
    router._adapters = {"claude": adapter}
    mock_router_cls.return_value = router
    mock_load.return_value = {"_project_root": str(tmp_path), "providers": {}}

    with patch("aidlc.cli_commands.input", side_effect=EOFError()):
        cmd_init(_args(project=str(tmp_path), with_docs=False, providers=True), version)


@patch("aidlc.auditor.CodeAuditor")
@patch("aidlc.routing.ProviderRouter")
@patch("aidlc.logger.setup_logger")
@patch("aidlc.cli_commands.load_config")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_audit_quick(
    mock_banner, mock_load, mock_log, mock_router, mock_auditor, version, tmp_path
):
    (tmp_path / "README.md").write_text("# x")
    mock_load.return_value = {"_project_root": str(tmp_path), "dry_run": True}
    ar = AuditResult(
        project_type="py",
        frameworks=["f"],
        modules=[MagicMock()],
        source_stats={"total_files": 3, "total_lines": 100},
        generated_docs=["STATUS.md"],
    )
    mock_auditor.return_value.run.return_value = ar
    cmd_audit(_args(project=str(tmp_path), full=False), version)


@patch("aidlc.auditor.CodeAuditor")
@patch("aidlc.routing.ProviderRouter")
@patch("aidlc.logger.setup_logger")
@patch("aidlc.cli_commands.load_config")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_audit_full_no_cli_exits(
    mock_banner, mock_load, mock_log, mock_router_cls, mock_auditor, version, tmp_path
):
    mock_load.return_value = {"_project_root": str(tmp_path), "dry_run": False}
    cli = MagicMock()
    cli.check_available.return_value = False
    mock_router_cls.return_value = cli
    with patch("aidlc.cli_commands.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_audit(_args(project=str(tmp_path), full=True), version)


@patch("aidlc.auditor.CodeAuditor")
@patch("aidlc.routing.ProviderRouter")
@patch("aidlc.logger.setup_logger")
@patch("aidlc.cli_commands.load_config")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_audit_with_conflicts_and_coverage(
    mock_banner, mock_load, mock_log, mock_router, mock_auditor, version, tmp_path
):
    mock_load.return_value = {"_project_root": str(tmp_path), "dry_run": True}
    tc = CoverageInfo(
        estimated_coverage="50%", test_framework="pytest", test_functions=1, source_files=1
    )
    c = AuditConflict(
        doc_path="ARCHITECTURE.md",
        field="summary",
        audit_value="a",
        user_value="b",
        severity="high",
    )
    ar = AuditResult(
        project_type="py",
        test_coverage=tc,
        tech_debt=[MagicMock()],
        conflicts=[c],
        generated_docs=["A.md"],
    )
    mock_auditor.return_value.run.return_value = ar
    cmd_audit(_args(project=str(tmp_path), full=False), version)


@patch("aidlc.improve.ImprovementCycle")
@patch("aidlc.scanner.ProjectScanner")
@patch("aidlc.routing.ProviderRouter")
@patch("aidlc.logger.setup_logger")
@patch("aidlc.cli_commands.load_config")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_improve_with_concern(
    mock_banner, mock_load, mock_log, mock_router, mock_scan_cls, mock_cycle, version, tmp_path
):
    mock_load.return_value = {"_project_root": str(tmp_path), "dry_run": True}
    mock_router.return_value.check_available.return_value = True
    inst = MagicMock()
    inst.scan.return_value = {
        "doc_files": [],
        "total_docs": 0,
        "project_type": "x",
        "existing_issues": [],
    }
    inst.build_context_prompt.return_value = "ctx"
    mock_scan_cls.return_value = inst
    mock_cycle.return_value.run.return_value = {"status": "complete", "implemented": 2}
    cmd_improve(_args(project=str(tmp_path), concern="fix bugs"), version)


@patch("aidlc.improve.ImprovementCycle")
@patch("aidlc.scanner.ProjectScanner")
@patch("aidlc.routing.ProviderRouter")
@patch("aidlc.logger.setup_logger")
@patch("aidlc.cli_commands.load_config")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_improve_prompts_concern_eof(
    mock_banner, mock_load, mock_log, mock_router, mock_scan_cls, mock_cycle, version, tmp_path
):
    mock_load.return_value = {"_project_root": str(tmp_path), "dry_run": True}
    mock_router.return_value.check_available.return_value = True
    inst = MagicMock()
    inst.scan.return_value = {
        "doc_files": [],
        "total_docs": 0,
        "project_type": "x",
        "existing_issues": [],
    }
    inst.build_context_prompt.return_value = "ctx"
    mock_scan_cls.return_value = inst
    with patch("aidlc.cli_commands.input", side_effect=EOFError()):
        cmd_improve(_args(project=str(tmp_path), concern=None), version)


@patch("aidlc.plan_session.PlanSession")
@patch("aidlc.routing.ProviderRouter")
@patch("aidlc.logger.setup_logger")
@patch("aidlc.cli_commands.load_config")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_plan(mock_banner, mock_load, mock_log, mock_router, mock_session, version, tmp_path):
    mock_load.return_value = {"_project_root": str(tmp_path), "dry_run": True}
    mock_router.return_value.check_available.return_value = True
    cmd_plan(
        _args(project=str(tmp_path), skip_wizard=True, wizard_only=False, review=False),
        version,
    )
    mock_session.return_value.run.assert_called_once()


@patch("aidlc.finalizer.Finalizer")
@patch("aidlc.scanner.ProjectScanner")
@patch("aidlc.routing.ProviderRouter")
@patch("aidlc.logger.setup_logger")
@patch("aidlc.cli_commands.find_latest_run")
@patch("aidlc.cli_commands.load_config")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_finalize(
    mock_banner,
    mock_load,
    mock_find,
    mock_log,
    mock_router,
    mock_scan,
    mock_fin,
    version,
    tmp_path,
):
    mock_load.return_value = {"_project_root": str(tmp_path), "dry_run": True}
    runs = tmp_path / ".aidlc" / "runs"
    run_dir = runs / "r1"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="r1", config_name="c")
    state.status = RunStatus.COMPLETE
    state.phase = RunPhase.DONE
    state.finalize_passes_completed = ["docs"]
    save_state(state, run_dir)
    mock_find.return_value = run_dir
    inst = MagicMock()
    inst.scan.return_value = {
        "doc_files": [],
        "total_docs": 0,
        "project_type": "x",
        "existing_issues": [],
    }
    inst.build_context_prompt.return_value = "ctx"
    mock_scan.return_value = inst
    mock_router.return_value.check_available.return_value = True
    cmd_finalize(_args(project=str(tmp_path), passes="docs,lint"), version)


@patch("aidlc.cli_commands._print_banner")
def test_cmd_status_no_runs_dir(mock_banner, version, tmp_path, capsys):
    cmd_status(_args(project=str(tmp_path)), version)
    out = capsys.readouterr().out
    assert "init" in out.lower() or "run" in out.lower()


@patch("aidlc.cli_commands.find_latest_run")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_status_shows_issues_and_audit(mock_banner, mock_find, version, tmp_path, capsys):
    runs = tmp_path / ".aidlc" / "runs"
    run_dir = runs / "r2"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="r2", config_name="c")
    state.status = RunStatus.RUNNING
    state.phase = RunPhase.PLANNING
    state.audit_depth = "quick"
    state.audit_completed = False
    state.stop_reason = "paused"
    state.issues = [
        {
            "id": "I-1",
            "title": "T",
            "status": "pending",
        }
    ]
    state.total_issues = 1
    save_state(state, run_dir)
    mock_find.return_value = run_dir
    cmd_status(_args(project=str(tmp_path)), version)
    assert "I-1" in capsys.readouterr().out


@patch("aidlc.cli.accounts.cmd_accounts")
def test_cmd_accounts_delegates(mock_acct, version):
    cmd_accounts(Namespace(), version)
    mock_acct.assert_called_once()


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.COMPLETE,
        RunStatus.FAILED,
        RunStatus.PAUSED,
        RunStatus.RUNNING,
    ],
)
@patch("aidlc.cli_commands.find_latest_run")
@patch("aidlc.cli_commands._print_banner")
def test_cmd_status_colored_status_strings(
    mock_banner, mock_find, status, version, tmp_path, capsys
):
    runs = tmp_path / ".aidlc" / "runs"
    run_dir = runs / "r3"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="r3", config_name="c")
    state.status = status
    save_state(state, run_dir)
    mock_find.return_value = run_dir
    cmd_status(_args(project=str(tmp_path)), version)
    assert state.run_id in capsys.readouterr().out
