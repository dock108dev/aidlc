"""Claude CLI adapter error paths and health checks."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from aidlc.claude_cli import ClaudeCLIError
from aidlc.providers.base import HealthStatus
from aidlc.providers.claude_adapter import ClaudeCLIAdapter


def test_execute_prompt_maps_claude_cli_error():
    log = logging.getLogger("tclaude")
    cfg = {"providers": {"claude": {"default_model": "opus"}}}
    ad = ClaudeCLIAdapter(cfg, log)
    with patch.object(ad._cli, "execute_prompt", side_effect=ClaudeCLIError("boom")):
        r = ad.execute_prompt("p", Path("/tmp"))
    assert r["success"] is False
    assert "boom" in r["error"]
    assert r["failure_type"] == "provider_error"


@patch("subprocess.run")
def test_validate_health_file_not_found(mock_run):
    mock_run.side_effect = FileNotFoundError()
    log = logging.getLogger("tclaude")
    ad = ClaudeCLIAdapter({"providers": {"claude": {}}}, log)
    hr = ad.validate_health()
    assert hr.status == HealthStatus.NOT_INSTALLED


@patch("subprocess.run")
def test_validate_health_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=1)
    log = logging.getLogger("tclaude")
    ad = ClaudeCLIAdapter({"providers": {"claude": {}}}, log)
    hr = ad.validate_health()
    assert hr.status == HealthStatus.UNREACHABLE


@patch("subprocess.run")
def test_validate_health_nonzero(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stderr="bad", stdout="")
    log = logging.getLogger("tclaude")
    ad = ClaudeCLIAdapter({"providers": {"claude": {}}}, log)
    hr = ad.validate_health()
    assert hr.status == HealthStatus.NOT_AUTHENTICATED


@patch("subprocess.run")
def test_validate_health_ok(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="2.0.0\n", stderr="")
    log = logging.getLogger("tclaude")
    ad = ClaudeCLIAdapter({"providers": {"claude": {}}}, log)
    hr = ad.validate_health()
    assert hr.status == HealthStatus.HEALTHY


def test_get_default_model_phase_and_fallback():
    log = logging.getLogger("tclaude")
    cfg = {
        "providers": {
            "claude": {
                "phase_models": {"planning": "m-plan"},
                "default_model": "m-def",
            }
        }
    }
    ad = ClaudeCLIAdapter(cfg, log)
    assert ad.get_default_model("planning") == "m-plan"
    assert ad.get_default_model() == "m-def"


def test_get_default_model_non_dict_providers():
    log = logging.getLogger("tclaude")
    ad = ClaudeCLIAdapter({"providers": "broken"}, log)
    assert ad.get_default_model("x") == "unknown"
