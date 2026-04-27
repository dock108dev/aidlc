"""Claude CLI integration for AIDLC runner.

Shells out to `claude` CLI. The CLI must be installed and authenticated.
"""

import json
import logging
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from .claude_cli_metadata import (
    extract_cli_metadata as _extract_cli_metadata_impl,
)
from .claude_cli_metadata import (
    extract_text_from_message as _extract_text_from_message_impl,
)

_TRANSIENT_PATTERNS = re.compile(
    r"rate.?limit|connection|timeout|API error|overloaded|503|502|429|ECONNRESET",
    re.IGNORECASE,
)
_SERVICE_OUTAGE_PATTERNS = re.compile(
    (
        r"\b500\b|internal server error|service unavailable|temporarily unavailable|"
        r"bad gateway|gateway timeout|upstream|network is unreachable|dns|eai_again|"
        r"could not resolve|name or service not known"
    ),
    re.IGNORECASE,
)


def _compact_text(value: str | None, max_len: int = 240) -> str:
    """Compact multiline text for concise log output."""
    if not value:
        return ""
    compact = " ".join(value.split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


def _summarize_stream_event(line: str) -> str:
    """One-line description of a stream-json event for heartbeat logs.

    Returns an empty string for unparseable or empty lines so the heartbeat
    reporter can pick a non-empty summary by walking backward.
    """
    raw = (line or "").strip()
    if not raw or not raw.startswith("{"):
        return ""
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(event, dict):
        return ""
    kind = event.get("type")
    if kind == "system":
        sub = event.get("subtype", "")
        return f"system {sub}".strip()
    if kind == "result":
        sub = event.get("subtype", "")
        if event.get("is_error"):
            return f"result error ({sub})".strip()
        return f"result {sub}".strip()
    if kind in ("assistant", "user"):
        msg = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name") or "?"
                inp = block.get("input") or {}
                # Most Claude tools put a short descriptive field like path/
                # file_path/command up front; surface the first one that looks
                # short and non-secret.
                hint = ""
                for key in ("file_path", "path", "command", "url", "query", "pattern"):
                    val = inp.get(key) if isinstance(inp, dict) else None
                    if isinstance(val, str) and val:
                        hint = val if len(val) <= 60 else val[:57] + "..."
                        break
                return f"tool_use {name}({hint})" if hint else f"tool_use {name}"
            if btype == "tool_result":
                return f"tool_result ({kind})"
            if btype == "text":
                text = block.get("text") or ""
                return f"assistant_text {len(text)} chars"
            if btype == "thinking":
                text = block.get("thinking") or ""
                return f"thinking {len(text)} chars"
        return kind
    return str(kind or "event")


def _pick_last_nonempty_summary(lines: list[str]) -> str:
    """Walk backward through collected stream lines for a meaningful summary."""
    for line in reversed(lines):
        summary = _summarize_stream_event(line)
        if summary:
            return summary
    return "no events yet"


def _stream_reader(stream, sink: list[str], last_activity_at: list[float]) -> None:
    """Background thread: read lines from stream, append to sink, stamp activity.

    Treats anything that is not a non-empty string as EOF. This is defensive
    against tests that mock `stream.readline` with a MagicMock (which would
    otherwise never compare equal to "") and against any real stream that
    gets closed mid-read.
    """
    try:
        while True:
            try:
                raw = stream.readline()
            except (ValueError, OSError):
                break
            if not isinstance(raw, str) or raw == "":
                break
            sink.append(raw)
            last_activity_at[0] = time.time()
    finally:
        try:
            stream.close()
        except (OSError, ValueError, AttributeError):
            pass


class ClaudeCLIError(Exception):
    pass


class ClaudeCLI:
    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger
        providers_cfg = config.get("providers", {})
        if not isinstance(providers_cfg, dict):
            providers_cfg = {}
        claude_cfg = providers_cfg.get("claude", {})
        if not isinstance(claude_cfg, dict):
            claude_cfg = {}
        self.cli_command = str(claude_cfg.get("cli_command", "claude"))
        self.model = str(claude_cfg.get("default_model", "opus"))
        self.max_retries = config.get("retry_max_attempts", 2)
        self.retry_base_delay = config.get("retry_base_delay_seconds", 30)
        self.retry_max_delay = config.get("retry_max_delay_seconds", 300)
        self.retry_backoff_factor = config.get("retry_backoff_factor", 2.0)
        self.dry_run = config.get("dry_run", False)

    def execute_prompt(
        self,
        prompt: str,
        working_dir: Path,
        allow_edits: bool = False,
        model_override: str | None = None,
    ) -> dict:
        """Execute a prompt via Claude CLI.

        Args:
            prompt: The prompt text
            working_dir: Directory to run claude from
            allow_edits: If True, uses --dangerously-skip-permissions so Claude
                         can edit files directly during implementation
            model_override: Use a specific model for this call (e.g., "sonnet", "opus")

        Returns:
            dict with: success, output, error, failure_type, duration_seconds, retries
        """
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Prompt ({len(prompt)} chars) in {working_dir}")
            return {
                "success": True,
                "output": "[DRY RUN] No execution",
                "error": None,
                "failure_type": None,
                "duration_seconds": 0.0,
                "retries": 0,
                "usage": {},
                "total_cost_usd": None,
                "model_used": model_override or self.model,
                "usage_source": "dry_run",
            }

        model = model_override or self.model
        # stream-json + verbose: one JSON event per line. Each line gives us a
        # natural liveness signal, and the terminal `result` event carries the
        # same `result`/`usage`/`total_cost_usd` fields the old single-JSON
        # format did, so the downstream parser still works via its JSONL
        # fallback path.
        cmd = [
            self.cli_command,
            "--print",
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if allow_edits:
            cmd.append("--dangerously-skip-permissions")

        warn_interval = max(1, int(self.config.get("claude_long_run_warn_seconds", 300)))
        # Activity-based stall detection only — wall-clock kills were removed
        # because Claude CLI in stream-json mode emits steady tool-use events
        # while doing real work, sometimes for an hour+. A wall-clock cap
        # interrupts productive sessions mid-output, leaving partial JSON
        # that downstream parsers then mishandle.
        stall_warn_raw = self.config.get("claude_stall_warn_seconds", 300)
        stall_warn = max(0, int(stall_warn_raw if stall_warn_raw is not None else 0))
        stall_kill_raw = self.config.get("claude_stall_kill_seconds", 0)
        stall_kill = max(0, int(stall_kill_raw if stall_kill_raw is not None else 0))
        # Post-terminal hang: once the model has emitted the terminal
        # ``result success`` / ``result error`` stream event, the CLI
        # should drain stdout and exit. In practice it sometimes hangs
        # for minutes afterwards (CLI bug, OS buffer, environment issue).
        # Once we've seen the terminal event, idle past this threshold
        # is unambiguously a hung CLI — kill it. Default 30s is much
        # shorter than ``claude_stall_kill_seconds`` because real work
        # cannot be happening (the model is done); a 200s gap during
        # tool calls is fine, a 30s gap *after* result is not.
        post_terminal_raw = self.config.get("claude_post_terminal_idle_seconds", 30)
        post_terminal_idle = max(0, int(post_terminal_raw if post_terminal_raw is not None else 0))
        timeout_grace = max(1, int(self.config.get("claude_timeout_grace_seconds", 30)))
        outage_max_wait = max(
            0, int(self.config.get("claude_service_outage_max_wait_seconds", 7200))
        )

        retries = 0
        last_failure_type = None
        last_error = None
        last_duration = 0.0
        attempt = 0
        outage_started_at: float | None = None
        outage_retry_attempt = 0
        outage_budget_exceeded = False
        while True:
            attempt += 1
            start = time.time()
            try:
                if outage_started_at is not None:
                    outage_elapsed = max(0.0, time.time() - outage_started_at)
                    outage_remaining = max(0.0, outage_max_wait - outage_elapsed)
                    self.logger.debug(
                        "Claude CLI outage retry attempt "
                        f"{attempt} (elapsed={outage_elapsed:.0f}s, remaining={outage_remaining:.0f}s)"
                    )
                else:
                    self.logger.debug(f"Claude CLI attempt {attempt}/{self.max_retries + 1}")

                # No wall-clock timeout — Claude CLI in stream-json mode
                # emits steady tool-use events even on multi-hour work.
                # Background reader threads stamp last-activity on every
                # stream line so the heartbeat can distinguish "still
                # working" from "stalled"; the only kill path is
                # stall_kill (opt-in safety valve for unattended runs).
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(working_dir),
                )
                # Feed prompt and close stdin
                proc.stdin.write(prompt)
                proc.stdin.close()

                stdout_lines: list[str] = []
                stderr_lines: list[str] = []
                last_activity_at = [time.time()]

                stdout_thread = threading.Thread(
                    target=_stream_reader,
                    args=(proc.stdout, stdout_lines, last_activity_at),
                    daemon=True,
                )
                stderr_thread = threading.Thread(
                    target=_stream_reader,
                    args=(proc.stderr, stderr_lines, last_activity_at),
                    daemon=True,
                )
                stdout_thread.start()
                stderr_thread.start()

                # Activity-aware wait loop. Emit a heartbeat every warn_interval
                # seconds of wall clock; flip to WARNING when idle crosses
                # stall_warn. Two kill paths (both opt-in by config):
                #   - stall_kill: idle past N seconds, anywhere in the run
                #     (broad runaway protection; default off).
                #   - post-terminal: idle past N seconds *after* the model
                #     emitted the terminal `result` event (targeted CLI-
                #     hang protection; default 30s).
                timed_out = False
                timeout_forced = False
                heartbeat_count = 0
                next_heartbeat_at = start + warn_interval
                saw_terminal = False  # flips True once a `result *` event lands

                while proc.poll() is None:
                    try:
                        proc.wait(timeout=1.0)
                        continue
                    except subprocess.TimeoutExpired:
                        pass

                    now = time.time()
                    elapsed = now - start
                    idle = now - last_activity_at[0]

                    # Detect the terminal event once. After it, the model is
                    # done; only CLI shutdown remains. Re-scanning the line
                    # list each iteration is cheap (walks backward to first
                    # non-empty event); we stop scanning once detected.
                    if not saw_terminal and idle >= 2:
                        last_event_brief = _pick_last_nonempty_summary(stdout_lines)
                        if last_event_brief.startswith("result "):
                            saw_terminal = True
                            self.logger.info(
                                f"Claude CLI emitted terminal '{last_event_brief}' event "
                                f"(elapsed={elapsed:.0f}s); will kill if the process does "
                                f"not exit within {post_terminal_idle}s of further silence."
                            )

                    if now >= next_heartbeat_at:
                        heartbeat_count += 1
                        next_heartbeat_at = now + warn_interval
                        last_event = _pick_last_nonempty_summary(stdout_lines)
                        if saw_terminal:
                            self.logger.info(
                                "Claude CLI post-terminal "
                                f"(elapsed={elapsed:.0f}s, idle={idle:.0f}s, "
                                f"last: {last_event}, model={model})"
                            )
                        elif stall_warn and idle >= stall_warn:
                            self.logger.warning(
                                "Claude CLI STALLED "
                                f"(elapsed={elapsed:.0f}s, idle={idle:.0f}s, "
                                f"last: {last_event}, model={model})"
                            )
                        else:
                            self.logger.info(
                                "Claude CLI working "
                                f"(elapsed={elapsed:.0f}s, idle={idle:.0f}s, "
                                f"last: {last_event}, model={model})"
                            )

                    kill_reason = None
                    if saw_terminal and post_terminal_idle and idle >= post_terminal_idle:
                        kill_reason = (
                            f"post-terminal hang ({idle:.0f}s of silence after the model's "
                            "terminal result event — model is done, CLI is not exiting)"
                        )
                        timed_out = True
                    elif stall_kill and idle >= stall_kill:
                        kill_reason = f"stall kill (no output for {idle:.0f}s)"
                        timed_out = True

                    if kill_reason:
                        self.logger.warning(
                            f"Claude CLI exceeded {kill_reason}. "
                            f"Requesting graceful stop (grace={timeout_grace}s)."
                        )
                        self._request_graceful_stop(proc)
                        try:
                            proc.wait(timeout=timeout_grace)
                        except subprocess.TimeoutExpired:
                            timeout_forced = True
                            self.logger.warning(
                                "Claude CLI did not stop gracefully; forcing termination."
                            )
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=5)
                        break

                # Let reader threads drain any final bytes.
                stdout_thread.join(timeout=2.0)
                stderr_thread.join(timeout=2.0)

                duration = time.time() - start
                stdout = "".join(stdout_lines)
                stderr = "".join(stderr_lines)
                returncode = proc.returncode if proc.returncode is not None else 124
                if timeout_forced and returncode == 0:
                    returncode = 124

                if returncode == 0:
                    if timed_out:
                        self.logger.info(
                            "Claude CLI exited cleanly after timeout stop request; accepting output."
                        )
                    output_text, usage, total_cost_usd, model_used, usage_source = (
                        self._extract_cli_metadata(stdout, model)
                    )
                    self.logger.debug(
                        "Claude CLI completed successfully "
                        f"(duration={duration:.1f}s, stdout_chars={len(stdout)}, retries={retries})"
                    )
                    return {
                        "success": True,
                        "output": output_text,
                        "error": None,
                        "failure_type": None,
                        "duration_seconds": duration,
                        "retries": retries,
                        "usage": usage,
                        "total_cost_usd": total_cost_usd,
                        "model_used": model_used,
                        "usage_source": usage_source,
                    }
                else:
                    stderr_text = stderr or ""
                    stdout_text = stdout or ""
                    if timed_out:
                        failure_type = "timeout"
                        if not stderr_text and not stdout_text:
                            stderr_text = "Claude CLI timed out"
                    elif self._is_service_outage(returncode, stderr_text, stdout_text):
                        failure_type = "service_down"
                    else:
                        failure_type = self._classify_failure(
                            returncode, f"{stderr_text}\n{stdout_text}"
                        )
                    last_failure_type = failure_type
                    stderr_snippet = _compact_text(stderr_text, 320)
                    stdout_snippet = _compact_text(stdout_text, 320)
                    reason_snippet = stderr_snippet or stdout_snippet or "no stderr/stdout captured"
                    last_error = reason_snippet[:500]
                    last_duration = duration
                    self.logger.warning(
                        "Claude CLI failed "
                        f"(attempt={attempt + 1}/{self.max_retries + 1}, rc={returncode}, "
                        f"failure_type={failure_type}, duration={duration:.1f}s, "
                        f"stderr_chars={len(stderr_text)}, stdout_chars={len(stdout_text)})"
                    )
                    self.logger.warning(f"Claude CLI failure detail: {reason_snippet}")
                    retries += 1
                    if failure_type == "service_down":
                        if outage_started_at is None:
                            outage_started_at = time.time()
                            self.logger.warning(
                                "Claude service appears offline (5xx/network outage). "
                                f"Will keep retrying with exponential backoff for up to "
                                f"{outage_max_wait:.0f}s."
                            )
                        outage_elapsed = max(0.0, time.time() - outage_started_at)
                        outage_remaining = max(0.0, outage_max_wait - outage_elapsed)
                        if outage_remaining <= 0:
                            outage_budget_exceeded = True
                            self.logger.error(
                                "Claude service outage exceeded retry window "
                                f"({outage_max_wait:.0f}s)."
                            )
                            break
                        delay = min(self._retry_delay(outage_retry_attempt), outage_remaining)
                        outage_retry_attempt += 1
                        self.logger.info(
                            "Retrying Claude CLI "
                            f"in {delay:.0f}s due to service outage "
                            f"(outage_elapsed={outage_elapsed:.0f}s, "
                            f"outage_remaining={outage_remaining:.0f}s)"
                        )
                        time.sleep(delay)
                        continue
                    if attempt <= self.max_retries:
                        delay = self._retry_delay(attempt - 1)
                        next_attempt = attempt + 1
                        self.logger.info(
                            "Retrying Claude CLI "
                            f"in {delay:.0f}s (next attempt {next_attempt}/{self.max_retries + 1})"
                        )
                        time.sleep(delay)
                        continue
                    break

            except FileNotFoundError:
                raise ClaudeCLIError(
                    f"Claude CLI not found at '{self.cli_command}'. "
                    "Install it or set providers.claude.cli_command in config."
                )

        if outage_budget_exceeded:
            return {
                "success": False,
                "output": None,
                "error": (
                    "Claude has been unavailable for an extended period (2h outage window reached). "
                    "Please check Claude status and retry when service is available."
                ),
                "failure_type": "service_down",
                "duration_seconds": last_duration,
                "retries": retries,
                "usage": {},
                "total_cost_usd": None,
                "model_used": model,
                "usage_source": "none",
            }
        return {
            "success": False,
            "output": None,
            "error": f"Failed after {retries} retries{': ' + last_error if last_error else ''}",
            "failure_type": last_failure_type or "transient",
            "duration_seconds": last_duration,
            "retries": retries,
            "usage": {},
            "total_cost_usd": None,
            "model_used": model,
            "usage_source": "none",
        }

    # Metadata parsing lives in claude_cli_metadata.py; these stubs preserve
    # the historical ClaudeCLI._extract_*  call surface used by tests and
    # by execute_prompt.
    _extract_cli_metadata = staticmethod(_extract_cli_metadata_impl)
    _extract_text_from_message = staticmethod(_extract_text_from_message_impl)

    @staticmethod
    def _request_graceful_stop(proc: subprocess.Popen) -> None:
        """Ask Claude process to stop cleanly before forcing termination."""
        try:
            proc.send_signal(signal.SIGINT)
        except (AttributeError, ValueError, ProcessLookupError):
            proc.terminate()

    def _retry_delay(self, attempt: int) -> float:
        """Calculate retry delay with exponential backoff and jitter."""
        import random

        delay = self.retry_base_delay * (self.retry_backoff_factor**attempt)
        delay = min(delay, self.retry_max_delay)
        # Add up to 25% jitter to avoid thundering herd
        jitter = delay * 0.25 * random.random()
        return delay + jitter

    @staticmethod
    def _classify_failure(returncode: int, stderr: str) -> str:
        if returncode > 128 or returncode < 0:
            return "transient"
        if _TRANSIENT_PATTERNS.search(stderr):
            return "transient"
        return "issue"

    @staticmethod
    def _is_service_outage(returncode: int, stderr: str, stdout: str) -> bool:
        if returncode in (500, 502, 503, 504):
            return True
        combined = f"{stderr}\n{stdout}"
        return bool(_SERVICE_OUTAGE_PATTERNS.search(combined))

    def check_available(self) -> bool:
        if self.dry_run:
            return True
        try:
            result = subprocess.run(
                [self.cli_command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
