"""Extra coverage for aidlc.reporting."""

from pathlib import Path

from aidlc.models import RunPhase, RunState, RunStatus
from aidlc.reporting import (
    _checkpoint_provider_markdown,
    generate_checkpoint_summary,
    generate_run_report,
)


def _minimal_state(**kwargs):
    s = RunState(run_id="rid-1", config_name="default", project_root="/tmp/proj")
    s.status = RunStatus.RUNNING
    s.phase = RunPhase.PLANNING
    s.started_at = "2020-01-01T00:00:00Z"
    s.last_updated = "2020-01-01T01:00:00Z"
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def test_run_report_includes_audit_block(tmp_path):
    s = _minimal_state(
        audit_depth="quick",
        audit_completed=True,
        audit_conflicts=[{"a": 1}],
    )
    p = generate_run_report(s, tmp_path)
    text = p.read_text()
    assert "## Audit Summary" in text
    assert "| Conflicts | 1 |" in text


def test_run_report_skips_non_dict_model_metrics(tmp_path):
    s = _minimal_state(
        claude_model_usage={
            "bad": "not-a-dict",
            "good": {
                "calls": 2,
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cost_usd_exact": 0.01,
                "cost_usd_estimated": 0.02,
            },
        }
    )
    body = generate_run_report(s, tmp_path).read_text()
    assert "good" in body
    assert "bad" not in body


def test_run_report_skips_non_dict_provider_and_phase_rows(tmp_path):
    s = _minimal_state(
        provider_account_usage={
            "openai": {
                "acc": {"calls": 1},
                "bad_acc": "x",
            },
        },
        phase_usage={
            "planning": {"provider_id": "p", "account_id": "a", "model": "m", "calls": 1},
            "bad_phase": [],
        },
    )
    body = generate_run_report(s, tmp_path).read_text()
    assert "openai" in body
    assert "planning" in body


def test_run_report_routing_fallbacks_table(tmp_path):
    s = _minimal_state(
        routing_decisions=[
            {
                "fallback": True,
                "phase": "planning",
                "provider_id": "p",
                "account_id": "a",
                "model": "m",
                "reasoning": "x" * 80,
            },
            {"fallback": False, "phase": "x"},
        ]
    )
    body = generate_run_report(s, tmp_path).read_text()
    assert "### Routing Fallbacks" in body


def test_run_report_validation_and_finalization(tmp_path):
    s = _minimal_state(
        validation_cycles=1,
        validation_issues_created=2,
        validation_test_results=[
            {"cycle": 1, "passed": True, "failure_count": 0},
            {"cycle": 2, "passed": False, "failure_count": 3},
        ],
        finalize_passes_requested=["docs", "lint"],
        finalize_passes_completed=["docs"],
    )
    body = generate_run_report(s, tmp_path).read_text()
    assert "## Validation Summary" in body
    assert "FAILED (3 failures)" in body
    assert "## Finalization Summary" in body
    assert "| lint | skipped |" in body


def test_run_report_notes_and_non_dict_artifact(tmp_path):
    s = _minimal_state(
        notes="Ship it",
        created_artifacts=[{"path": "a.md", "type": "doc", "action": "create"}, "raw-string"],
    )
    body = generate_run_report(s, tmp_path).read_text()
    assert "## Notes" in body
    assert "raw-string" in body


def test_checkpoint_provider_markdown_malformed_nested():
    out = _checkpoint_provider_markdown(
        {
            "p1": "not-dict",
            "p2": {
                "a1": {"calls": 1, "calls_succeeded": 1, "calls_failed": 0},
                "a2": "bad-metrics",
            },
        }
    )
    assert "p2" in out
    assert "a1" in out


def test_generate_checkpoint_summary_coerces_non_dict_usage(tmp_path):
    s = _minimal_state(
        checkpoint_count=7,
        provider_account_usage="broken",
    )
    p = generate_checkpoint_summary(s, tmp_path)
    assert p.name == "checkpoint_0007.md"
    assert "Checkpoint 7" in p.read_text()
