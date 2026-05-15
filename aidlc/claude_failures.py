"""Claude CLI failure classification helpers."""

from __future__ import annotations

import random
import re
import signal
import subprocess

TRANSIENT_PATTERNS = re.compile(
    r"rate.?limit|connection|timeout|API error|overloaded|503|502|429|ECONNRESET",
    re.IGNORECASE,
)
SERVICE_OUTAGE_PATTERNS = re.compile(
    (
        r"\b500\b|internal server error|service unavailable|temporarily unavailable|"
        r"bad gateway|gateway timeout|upstream|network is unreachable|dns|eai_again|"
        r"could not resolve|name or service not known"
    ),
    re.IGNORECASE,
)
SESSION_ID_IN_USE = re.compile(
    r"session\s+id\s+\S+\s+is\s+already\s+in\s+use",
    re.IGNORECASE,
)


def request_graceful_stop(proc: subprocess.Popen) -> None:
    """Ask Claude process to stop cleanly before forcing termination."""
    try:
        proc.send_signal(signal.SIGINT)
    except (AttributeError, ValueError, ProcessLookupError):
        proc.terminate()


def retry_delay(cli, attempt: int) -> float:
    """Calculate retry delay with exponential backoff and jitter."""
    delay = cli.retry_base_delay * (cli.retry_backoff_factor**attempt)
    delay = min(delay, cli.retry_max_delay)
    jitter = delay * 0.25 * random.random()
    return delay + jitter


def classify_failure(returncode: int, stderr: str) -> str:
    if returncode > 128 or returncode < 0:
        return "transient"
    if TRANSIENT_PATTERNS.search(stderr):
        return "transient"
    return "issue"


def is_service_outage(returncode: int, stderr: str, stdout: str) -> bool:
    if returncode in (500, 502, 503, 504):
        return True
    combined = f"{stderr}\n{stdout}"
    if SERVICE_OUTAGE_PATTERNS.search(combined):
        return True
    if returncode != 0 and stdout:
        head = stdout[:4096]
        if '"subtype":"init"' in head and '"type":"result"' not in stdout:
            return True
    return False


def check_available(cli) -> bool:
    if cli.dry_run:
        return True
    try:
        result = subprocess.run(
            [cli.cli_command, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
