"""Subprocess helper that reaps entire process trees on timeout/exception.

`subprocess.run(..., shell=True, timeout=...)` only kills the direct child
(the shell); descendants such as pytest-xdist workers, node test runners,
godot --headless, playwright browsers, etc. get reparented to init and
linger, accumulating across repeated implementation cycles. This helper
launches the command in a new process group (POSIX) or with
CREATE_NEW_PROCESS_GROUP (Windows) so the whole tree can be signaled as
a unit.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def run_with_group_kill(
    cmd: str,
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    grace_seconds: float = 2.0,
) -> ProcResult:
    """Run `cmd` via the shell, killing the entire process group on timeout."""
    popen_kwargs: dict = {
        "shell": True,
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_group(proc, grace_seconds)
        try:
            stdout, stderr = proc.communicate(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    except BaseException:
        _terminate_group(proc, grace_seconds)
        raise

    rc = proc.returncode if proc.returncode is not None else -1
    return ProcResult(
        returncode=rc,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
    )


def _terminate_group(proc: subprocess.Popen, grace_seconds: float) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
        return

    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        return

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
