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
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(ch)

    # Errors only
    eh = logging.FileHandler(log_dir / f"{run_id}.errors.log", encoding="utf-8")
    eh.setLevel(logging.ERROR)
    eh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(eh)

    return logger


def log_checkpoint(logger: logging.Logger, state_dict: dict) -> None:
    elapsed_h = state_dict.get("elapsed_seconds", 0) / 3600
    wall_h = state_dict.get("wall_clock_seconds", 0) / 3600
    phase = state_dict.get("phase", "?")
    logger.info("=" * 60)
    logger.info("CHECKPOINT")
    logger.info(f"  Phase: {phase}")
    logger.info(f"  Elapsed (Claude): {elapsed_h:.1f}h")
    logger.info(f"  Elapsed (wall):   {wall_h:.1f}h")
    logger.info(f"  Planning cycles: {state_dict.get('planning_cycles', 0)}")
    logger.info(f"  Issues created: {state_dict.get('issues_created', 0)}")
    logger.info(f"  Implementation cycles: {state_dict.get('implementation_cycles', 0)}")
    logger.info(f"  Issues implemented: {state_dict.get('issues_implemented', 0)}")
    logger.info(f"  Issues verified: {state_dict.get('issues_verified', 0)}")
    logger.info("=" * 60)
