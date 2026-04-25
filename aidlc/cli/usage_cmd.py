"""Usage aggregation and display CLI."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..state_manager import load_state
from .display import bold, cyan, dim, print_banner, yellow


def cmd_usage(args: argparse.Namespace, version: str) -> None:
    """Show token/cost usage table across runs."""
    project_root = Path(getattr(args, "project", None) or ".").resolve()
    runs_dir = project_root / ".aidlc" / "runs"
    by = getattr(args, "by", "provider")
    last_n = getattr(args, "last", 1)
    since_str = getattr(args, "since", None)

    print_banner(version)

    if not runs_dir.exists():
        print(f"  {dim('No runs found at')} {runs_dir}")
        print(f"  Run {cyan('aidlc run --dry-run')} to create a run first.")
        return

    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and (d / "state.json").exists()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    if last_n > 0:
        run_dirs = run_dirs[:last_n]

    if since_str:
        try:
            since_dt = datetime.strptime(since_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            run_dirs = [
                d
                for d in run_dirs
                if datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
                >= since_dt
            ]
        except ValueError:
            print(
                f"  {yellow('!')} Invalid --since date '{since_str}'. Use YYYY-MM-DD."
            )
            sys.exit(1)

    if not run_dirs:
        print("  No matching runs found.")
        return

    totals: dict[str, dict] = {}
    run_count = 0

    for run_dir in run_dirs:
        try:
            state = load_state(run_dir)
        except Exception:
            continue
        run_count += 1

        if by == "provider":
            pdata = getattr(state, "provider_account_usage", {}) or {}
            if pdata:
                for provider_id, acct_map in pdata.items():
                    for _acct_id, usage in acct_map.items():
                        _accumulate_usage(totals, provider_id, usage)
            else:
                _accumulate_legacy_usage(totals, "claude", state)

        elif by == "account":
            pdata = getattr(state, "provider_account_usage", {}) or {}
            if pdata:
                for provider_id, acct_map in pdata.items():
                    for acct_id, usage in acct_map.items():
                        _accumulate_usage(totals, f"{provider_id}/{acct_id}", usage)
            else:
                _accumulate_legacy_usage(totals, "claude/default", state)

        elif by == "phase":
            phase_data = getattr(state, "phase_usage", {}) or {}
            if phase_data:
                for phase, usage in phase_data.items():
                    _accumulate_usage(totals, phase, usage)
            else:
                _accumulate_legacy_usage(totals, "all_phases", state)

        elif by == "model":
            model_data = getattr(state, "claude_model_usage", {}) or {}
            if model_data:
                for model, usage in model_data.items():
                    _accumulate_usage(totals, model, usage)
            else:
                _accumulate_legacy_usage(totals, "unknown_model", state)

    if not totals:
        print("  No usage data found in selected run(s).")
        return

    col_key = max((len(k) for k in totals), default=10) + 2
    col_key = max(col_key, 22)
    header_key = by.capitalize() if by != "account" else "Provider/Account"

    print(f"  {bold(f'Usage — last {run_count} run(s), grouped by {by}')}")
    print()
    print(
        f"  {header_key:<{col_key}} {'Calls':>7} {'Success':>8} "
        f"{'Input tok':>11} {'Output tok':>11} {'Est. USD':>10}"
    )
    print(f"  {'-' * col_key} {'-' * 7} {'-' * 8} {'-' * 11} {'-' * 11} {'-' * 10}")

    grand = {"calls": 0, "succeeded": 0, "input": 0, "output": 0, "cost": 0.0}
    for key, row in sorted(totals.items()):
        cost = row.get("cost_usd_exact") or row.get("cost_usd_estimated") or 0.0
        print(
            f"  {key:<{col_key}} {row['calls']:>7} {row['succeeded']:>8} "
            f"{row['input_tokens']:>11,} {row['output_tokens']:>11,} "
            f"${cost:>9.4f}"
        )
        grand["calls"] += row["calls"]
        grand["succeeded"] += row["succeeded"]
        grand["input"] += row["input_tokens"]
        grand["output"] += row["output_tokens"]
        grand["cost"] += cost

    print(f"  {'─' * col_key} {'─' * 7} {'─' * 8} {'─' * 11} {'─' * 11} {'─' * 10}")
    print(
        f"  {bold('TOTAL'):<{col_key + 7}} {grand['calls']:>7} {grand['succeeded']:>8} "
        f"{grand['input']:>11,} {grand['output']:>11,} "
        f"${grand['cost']:>9.4f}"
    )
    print()


def _accumulate_usage(totals: dict, key: str, usage: dict) -> None:
    if key not in totals:
        totals[key] = {
            "calls": 0,
            "succeeded": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd_exact": 0.0,
            "cost_usd_estimated": 0.0,
        }
    t = totals[key]
    t["calls"] += usage.get("calls", 0)
    t["succeeded"] += usage.get("calls_succeeded", usage.get("succeeded", 0))
    t["input_tokens"] += usage.get("input_tokens", 0)
    t["output_tokens"] += usage.get("output_tokens", 0)
    t["cost_usd_exact"] += usage.get("cost_usd_exact", 0.0) or 0.0
    t["cost_usd_estimated"] += usage.get("cost_usd_estimated", 0.0) or 0.0


def _accumulate_legacy_usage(totals: dict, key: str, state) -> None:
    usage = {
        "calls": getattr(state, "claude_calls_total", 0),
        "calls_succeeded": getattr(state, "claude_calls_succeeded", 0),
        "input_tokens": getattr(state, "claude_total_input_tokens", 0),
        "output_tokens": getattr(state, "claude_output_tokens", 0),
        "cost_usd_exact": getattr(state, "claude_cost_usd_exact", 0.0) or 0.0,
        "cost_usd_estimated": getattr(state, "claude_cost_usd_estimated", 0.0) or 0.0,
    }
    _accumulate_usage(totals, key, usage)
