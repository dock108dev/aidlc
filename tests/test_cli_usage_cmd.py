"""Tests for aidlc.cli.usage_cmd."""

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aidlc.cli.usage_cmd import _accumulate_legacy_usage, _accumulate_usage, cmd_usage
from aidlc.models import RunState
from aidlc.state_manager import save_state


def _write_run(tmp_path: Path, run_id: str, **state_kwargs) -> Path:
    aidlc = tmp_path / ".aidlc" / "runs" / run_id
    aidlc.mkdir(parents=True)
    state = RunState(run_id=run_id, config_name="default")
    for k, v in state_kwargs.items():
        setattr(state, k, v)
    save_state(state, aidlc)
    return aidlc


def test_cmd_usage_no_runs_dir(tmp_path, capsys):
    args = argparse.Namespace(project=str(tmp_path), by="provider", last=1, since=None)
    cmd_usage(args, "0.0.0")
    out = capsys.readouterr().out
    assert "No runs" in out or "runs" in out.lower()


def test_cmd_usage_legacy_provider(tmp_path, capsys):
    runs = tmp_path / ".aidlc" / "runs"
    runs.mkdir(parents=True)
    _write_run(tmp_path, "run_a", claude_calls_total=2, claude_calls_succeeded=2)
    args = argparse.Namespace(project=str(tmp_path), by="provider", last=5, since=None)
    cmd_usage(args, "0.0.0")
    out = capsys.readouterr().out
    assert "claude" in out.lower() or "Usage" in out


def test_cmd_usage_by_account_with_provider_map(tmp_path, capsys):
    runs = tmp_path / ".aidlc" / "runs"
    runs.mkdir(parents=True)
    state = RunState(run_id="r2", config_name="default")
    state.provider_account_usage = {
        "openai": {"acct1": {"calls": 1, "calls_succeeded": 1, "input_tokens": 10, "output_tokens": 5}}
    }
    rd = runs / "r2"
    rd.mkdir(parents=True)
    save_state(state, rd)
    args = argparse.Namespace(project=str(tmp_path), by="account", last=5, since=None)
    cmd_usage(args, "0.0.0")
    assert "openai/acct1" in capsys.readouterr().out


def test_cmd_usage_by_phase(tmp_path, capsys):
    runs = tmp_path / ".aidlc" / "runs"
    runs.mkdir(parents=True)
    state = RunState(run_id="r3", config_name="default")
    state.phase_usage = {"planning": {"calls": 1, "calls_succeeded": 1, "input_tokens": 1, "output_tokens": 1}}
    rd = runs / "r3"
    rd.mkdir(parents=True)
    save_state(state, rd)
    args = argparse.Namespace(project=str(tmp_path), by="phase", last=5, since=None)
    cmd_usage(args, "0.0.0")
    assert "planning" in capsys.readouterr().out


def test_cmd_usage_by_model(tmp_path, capsys):
    runs = tmp_path / ".aidlc" / "runs"
    runs.mkdir(parents=True)
    state = RunState(run_id="r4", config_name="default")
    state.claude_model_usage = {"opus": {"calls": 1, "calls_succeeded": 1, "input_tokens": 2, "output_tokens": 3}}
    rd = runs / "r4"
    rd.mkdir(parents=True)
    save_state(state, rd)
    args = argparse.Namespace(project=str(tmp_path), by="model", last=5, since=None)
    cmd_usage(args, "0.0.0")
    assert "opus" in capsys.readouterr().out


def test_cmd_usage_invalid_since(tmp_path, monkeypatch):
    runs = tmp_path / ".aidlc" / "runs"
    runs.mkdir(parents=True)
    _write_run(tmp_path, "r5")
    args = argparse.Namespace(project=str(tmp_path), by="provider", last=1, since="not-a-date")
    monkeypatch.setattr(sys, "exit", lambda c: (_ for _ in ()).throw(SystemExit(c)))
    with pytest.raises(SystemExit):
        cmd_usage(args, "0.0.0")


def test_cmd_usage_no_matching_runs_after_since(tmp_path, capsys):
    runs = tmp_path / ".aidlc" / "runs"
    runs.mkdir(parents=True)
    _write_run(tmp_path, "old")
    args = argparse.Namespace(project=str(tmp_path), by="provider", last=10, since="2099-01-01")
    cmd_usage(args, "0.0.0")
    assert "No matching" in capsys.readouterr().out


def test_cmd_usage_corrupt_state_skipped(tmp_path, capsys):
    runs = tmp_path / ".aidlc" / "runs" / "bad"
    runs.mkdir(parents=True)
    (runs / "state.json").write_text("{not json")
    args = argparse.Namespace(project=str(tmp_path), by="provider", last=5, since=None)
    cmd_usage(args, "0.0.0")
    out = capsys.readouterr().out
    assert "No usage data" in out or "Usage" in out


def test_accumulate_usage_creates_and_merges():
    t = {}
    _accumulate_usage(t, "a", {"calls": 1, "calls_succeeded": 1, "input_tokens": 1, "output_tokens": 1})
    _accumulate_usage(t, "a", {"calls": 1, "succeeded": 1, "input_tokens": 2, "output_tokens": 0})
    assert t["a"]["calls"] == 2
    assert t["a"]["input_tokens"] == 3


def test_accumulate_legacy_usage():
    st = MagicMock()
    st.claude_calls_total = 1
    st.claude_calls_succeeded = 1
    st.claude_total_input_tokens = 3
    st.claude_output_tokens = 2
    st.claude_cost_usd_exact = 0.1
    st.claude_cost_usd_estimated = 0.0
    t = {}
    _accumulate_legacy_usage(t, "claude", st)
    assert t["claude"]["calls"] == 1
