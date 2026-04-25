"""Logging for AIDLC runner."""

import logging
import sys
from pathlib import Path


def setup_logger(run_id: str, log_dir: Path, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger(f"aidlc.{run_id}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    log_dir.mkdir(parents=True, exist_ok=True)

    # Full log file
    fh = logging.FileHandler(log_dir / f"{run_id}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(fh)

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(ch)

    # Errors only
    eh = logging.FileHandler(log_dir / f"{run_id}.errors.log", encoding="utf-8")
    eh.setLevel(logging.ERROR)
    eh.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(eh)

    return logger


def _log_provider_account_usage(logger: logging.Logger, provider_account_usage: object) -> None:
    """Log per-provider/account token and call stats from state.provider_account_usage."""
    if not isinstance(provider_account_usage, dict) or not provider_account_usage:
        logger.info("  Per provider: (no breakdown recorded)")
        return
    logger.info("  Per provider:")
    for provider_id, accounts in sorted(provider_account_usage.items()):
        if not isinstance(accounts, dict):
            continue
        for account_id, metrics in sorted(accounts.items()):
            if not isinstance(metrics, dict):
                continue
            cost_ex = float(metrics.get("cost_usd_exact", 0.0) or 0.0)
            cost_est = float(metrics.get("cost_usd_estimated", 0.0) or 0.0)
            total_tok = int(metrics.get("total_tokens", 0) or 0)
            logger.info(
                f"    {provider_id}/{account_id}: "
                f"calls={metrics.get('calls', 0)} ok={metrics.get('calls_succeeded', 0)} "
                f"fail={metrics.get('calls_failed', 0)} "
                f"in={metrics.get('input_tokens', 0)} out={metrics.get('output_tokens', 0)} "
                f"total={total_tok} "
                f"cost_exact={cost_ex:.4f} cost_est={cost_est:.4f}"
            )


def log_checkpoint(logger: logging.Logger, state_dict: dict) -> None:
    provider_h = state_dict.get("elapsed_seconds", 0) / 3600
    console_h = state_dict.get("console_seconds", 0) / 3600
    phase = state_dict.get("phase", "?")
    logger.info("=" * 60)
    logger.info("CHECKPOINT")
    logger.info(f"  Phase: {phase}")
    logger.info(f"  AI provider time:    {provider_h:.1f}h")
    logger.info(f"  Console (local) time: {console_h:.1f}h")
    logger.info(f"  Planning cycles: {state_dict.get('planning_cycles', 0)}")
    logger.info(f"  Issues created: {state_dict.get('issues_created', 0)}")
    logger.info(f"  Implementation cycles: {state_dict.get('implementation_cycles', 0)}")
    logger.info(f"  Issues implemented: {state_dict.get('issues_implemented', 0)}")
    logger.info(f"  Issues verified: {state_dict.get('issues_verified', 0)}")
    logger.info(
        "  Provider calls: "
        f"{state_dict.get('claude_calls_total', 0)} total, "
        f"{state_dict.get('claude_calls_succeeded', 0)} ok, "
        f"{state_dict.get('claude_calls_failed', 0)} failed, "
        f"{state_dict.get('claude_retries_total', 0)} retries"
    )
    # Field names are historical (claude_*); values are sums across all providers.
    logger.info(
        "  Aggregate usage (all providers; internal fields claude_*): "
        f"in={state_dict.get('claude_input_tokens', 0)}, "
        f"out={state_dict.get('claude_output_tokens', 0)}, "
        f"cache_write={state_dict.get('claude_cache_creation_input_tokens', 0)}, "
        f"cache_read={state_dict.get('claude_cache_read_input_tokens', 0)}, "
        f"total={state_dict.get('claude_total_tokens', 0)}"
    )
    _log_provider_account_usage(logger, state_dict.get("provider_account_usage"))
    logger.info(
        "  Provider tool requests: "
        f"web_search={state_dict.get('claude_web_search_requests', 0)}, "
        f"web_fetch={state_dict.get('claude_web_fetch_requests', 0)}"
    )
    logger.info(
        "  Provider cost (USD, all providers; exact only if CLI reported billing): "
        f"exact={state_dict.get('claude_cost_usd_exact', 0.0):.4f}, "
        f"estimated={state_dict.get('claude_cost_usd_estimated', 0.0):.4f}"
    )
    logger.info("=" * 60)
