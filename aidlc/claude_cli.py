"""Claude CLI integration for AIDLC runner.

Shells out to `claude` CLI. The CLI must be installed and authenticated.
"""

import json
import re
import signal
import subprocess
import time
from pathlib import Path
import logging

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
        cmd = [
            self.cli_command,
            "--print",
            "--model",
            model,
            "--output-format",
            "json",
        ]
        if allow_edits:
            cmd.append("--dangerously-skip-permissions")

        warn_interval = max(1, int(self.config.get("claude_long_run_warn_seconds", 300)))
        hard_timeout_raw = self.config.get("claude_hard_timeout_seconds")
        hard_timeout = max(0, int(hard_timeout_raw if hard_timeout_raw is not None else 1800))
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
                    self.logger.debug(
                        f"Claude CLI attempt {attempt}/{self.max_retries + 1}"
                    )

                # Run without timeout — let Claude finish. Warn periodically.
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

                # Wait with periodic warnings and optional hard timeout.
                timed_out = False
                timeout_forced = False
                still_running_warnings = 0
                while proc.poll() is None:
                    try:
                        proc.wait(timeout=warn_interval)
                    except subprocess.TimeoutExpired:
                        elapsed = time.time() - start
                        still_running_warnings += 1
                        timeout_status = (
                            f"hard timeout in {max(0, hard_timeout - elapsed):.0f}s"
                            if hard_timeout
                            else "no hard timeout configured"
                        )
                        self.logger.warning(
                            "Claude CLI still running "
                            f"(elapsed={elapsed:.0f}s, warn_count={still_running_warnings}, "
                            f"{timeout_status}, model={model})"
                        )
                        if hard_timeout and elapsed >= hard_timeout:
                            timed_out = True
                            self.logger.warning(
                                f"Claude CLI exceeded hard timeout ({hard_timeout}s). "
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

                duration = time.time() - start
                stdout = proc.stdout.read()
                stderr = proc.stderr.read()
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
                    self.logger.warning(
                        f"Claude CLI failure detail: {reason_snippet}"
                    )
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
            "error": f"Failed after {retries} retries"
            f"{': ' + last_error if last_error else ''}",
            "failure_type": last_failure_type or "transient",
            "duration_seconds": last_duration,
            "retries": retries,
            "usage": {},
            "total_cost_usd": None,
            "model_used": model,
            "usage_source": "none",
        }

    @staticmethod
    def _extract_cli_metadata(
        stdout: str,
        fallback_model: str,
    ) -> tuple[str, dict, float | None, str, str]:
        """Parse Claude CLI JSON output, returning text + usage metadata."""
        text = stdout or ""
        usage = {}
        total_cost_usd = None
        model_used = fallback_model
        usage_source = "none"

        if not text.strip():
            return text, usage, total_cost_usd, model_used, usage_source

        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None

        if not isinstance(parsed, dict):
            return text, usage, total_cost_usd, model_used, usage_source

        usage_source = "claude_cli_json"
        result_text = parsed.get("result")
        if not isinstance(result_text, str):
            message = parsed.get("message")
            if isinstance(message, dict):
                result_text = ClaudeCLI._extract_text_from_message(message)
            else:
                result_text = text

        parsed_usage = parsed.get("usage")
        if not isinstance(parsed_usage, dict):
            message = parsed.get("message")
            if isinstance(message, dict):
                parsed_usage = message.get("usage")
        if isinstance(parsed_usage, dict):
            usage = {
                "input_tokens": int(parsed_usage.get("input_tokens", 0) or 0),
                "output_tokens": int(parsed_usage.get("output_tokens", 0) or 0),
                "cache_creation_input_tokens": int(
                    parsed_usage.get("cache_creation_input_tokens", 0) or 0
                ),
                "cache_read_input_tokens": int(
                    parsed_usage.get("cache_read_input_tokens", 0) or 0
                ),
                "web_search_requests": int(
                    (
                        (parsed_usage.get("server_tool_use") or {}).get("web_search_requests", 0)
                        if isinstance(parsed_usage.get("server_tool_use"), dict)
                        else parsed_usage.get("web_search_requests", 0)
                    ) or 0
                ),
                "web_fetch_requests": int(
                    (
                        (parsed_usage.get("server_tool_use") or {}).get("web_fetch_requests", 0)
                        if isinstance(parsed_usage.get("server_tool_use"), dict)
                        else parsed_usage.get("web_fetch_requests", 0)
                    ) or 0
                ),
            }

        raw_cost = parsed.get("total_cost_usd")
        if raw_cost is None and isinstance(parsed.get("message"), dict):
            raw_cost = parsed["message"].get("total_cost_usd")
        try:
            total_cost_usd = float(raw_cost) if raw_cost is not None else None
        except (TypeError, ValueError):
            total_cost_usd = None

        raw_model = parsed.get("model")
        if raw_model is None and isinstance(parsed.get("message"), dict):
            raw_model = parsed["message"].get("model")
        if isinstance(raw_model, str) and raw_model.strip():
            model_used = raw_model

        return result_text, usage, total_cost_usd, model_used, usage_source

    @staticmethod
    def _extract_text_from_message(message: dict) -> str:
        """Extract concatenated text blocks from a Claude message object."""
        content = message.get("content")
        if not isinstance(content, list):
            return ""
        chunks = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "".join(chunks)

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
        delay = self.retry_base_delay * (self.retry_backoff_factor ** attempt)
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
