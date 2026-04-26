"""Extra tests for aidlc.cli.display."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from aidlc.cli import display


def test_color_helpers_plain_when_no_color():
    with patch.object(display, "_USE_COLOR", False):
        assert display.bold("a") == "a"
        assert display.green("a") == "a"
        assert display.yellow("a") == "a"
        assert display.red("a") == "a"
        assert display.dim("a") == "a"
        assert display.cyan("a") == "a"


def test_print_banner(capsys):
    with patch.object(display, "_USE_COLOR", False):
        display.print_banner("1.2.3")
    out = capsys.readouterr().out
    assert "AIDLC" in out
    assert "1.2.3" in out


def test_get_template_dir_returns_existing_dir():
    d = display.get_template_dir()
    assert d.name == "project_template"
    assert d.exists()


def test_print_precheck_not_ready(capsys):
    result = MagicMock()
    result.config_created = False
    result.has_source_code = False
    result.required_found = []
    result.ready = False
    display.print_precheck(result, Path("/tmp"), verbose=False)
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "BRAINDUMP.md" in out


def test_print_precheck_ready_when_braindump_present(capsys):
    result = MagicMock()
    result.config_created = True
    result.has_source_code = True
    result.project_type = "python"
    result.required_found = ["BRAINDUMP.md"]
    result.ready = True
    display.print_precheck(result, Path("/tmp"), verbose=False)
    out = capsys.readouterr().out
    assert "READY" in out
    assert "BRAINDUMP.md" in out


def test_print_precheck_has_source_hint(capsys):
    """When source code is detected, the precheck hint should mention the
    pre-planning discovery pass so users know the planner sees current state."""
    result = MagicMock()
    result.config_created = False
    result.has_source_code = True
    result.project_type = "python"
    result.required_found = ["BRAINDUMP.md"]
    result.ready = True
    display.print_precheck(result, Path("/tmp"), verbose=False)
    out = capsys.readouterr().out
    assert "discovery" in out.lower()
