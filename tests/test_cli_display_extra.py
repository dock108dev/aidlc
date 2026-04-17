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
    result.recommended_found = []
    result.optional_found = []
    result.score = "not ready"
    display.print_precheck(result, Path("/tmp"), verbose=False)
    out = capsys.readouterr().out
    assert "Readiness" in out


def test_print_precheck_excellent(capsys):
    from aidlc.precheck import OPTIONAL_DOCS, RECOMMENDED_DOCS

    result = MagicMock()
    result.config_created = True
    result.has_source_code = True
    result.required_found = []
    result.recommended_found = list(RECOMMENDED_DOCS.keys())
    result.optional_found = list(OPTIONAL_DOCS.keys())
    result.score = "excellent"
    display.print_precheck(result, Path("/tmp"), verbose=True)
    assert "EXCELLENT" in capsys.readouterr().out


def test_print_precheck_good_and_verbose_recommended(capsys):
    result = MagicMock()
    result.config_created = False
    result.has_source_code = True
    result.required_found = []
    result.recommended_found = []
    result.optional_found = []
    result.score = "good"
    display.print_precheck(result, Path("/tmp"), verbose=True)
    out = capsys.readouterr().out
    assert "GOOD" in out


def test_print_precheck_minimal_score(capsys):
    result = MagicMock()
    result.config_created = False
    result.has_source_code = False
    result.required_found = []
    result.recommended_found = []
    result.optional_found = []
    result.score = "minimal"
    display.print_precheck(result, Path("/tmp"), verbose=False)
    assert "MINIMAL" in capsys.readouterr().out


def test_print_precheck_has_source_and_status_hint(capsys):
    result = MagicMock()
    result.config_created = False
    result.has_source_code = True
    result.project_type = "python"
    result.required_found = []
    result.recommended_found = []
    result.optional_found = []
    result.score = "good"
    display.print_precheck(result, Path("/tmp"), verbose=False)
    out = capsys.readouterr().out
    assert "audit" in out.lower() or "Project" in out
