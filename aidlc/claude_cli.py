"""Claude CLI integration for AIDLC runner.

Shells out to `claude` CLI. The CLI must be installed and authenticated.
"""

import re
import subprocess
import time
from pathlib import Path
import logging

_TRANSIENT_PATTERNS = re.compile(
    r"rate.?limit|connection|timeout|API error|overloaded|503|502|429|ECONNRESET",
    re.IGNORECASE,
)


class ClaudeCLIError(Exception):
    pass


class ClaudeCLI:
    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.cli_command = config.get("claude_cli_command", "claude")
        self.model = config.get("claude_model", "opus")
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
            }

        model = model_override or self.model
        cmd = [self.cli_command, "--print", "--model", model]
        if allow_edits:
            cmd.append("--dangerously-skip-permissions")

        warn_interval = self.config.get("claude_long_run_warn_seconds", 300)
        hard_timeout = self.config.get("claude_hard_timeout_seconds", 0)

        retries = 0
        last_failure_type = None
        last_error = None
        last_duration = 0.0
        for attempt in range(self.max_retries + 1):
            start = time.time()
            try:
                self.logger.debug(f"Claude CLI attempt {attempt + 1}/{self.max_retries + 1}")

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

                # Wait with periodic warnings instead of hard timeout
                timed_out = False
                while proc.poll() is None:
                    elapsed = time.time() - start
                    if hard_timeout and elapsed >= hard_timeout:
                        timed_out = True
                        self.logger.warning(
                            f"Claude CLI exceeded hard timeout ({hard_timeout}s). "
                            "Terminating process."
                        )
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=5)
                        break
                    try:
                        proc.wait(timeout=warn_interval)
                    except subprocess.TimeoutExpired:
                        self.logger.warning(
                            f"Claude CLI still running ({elapsed:.0f}s elapsed)..."
                        )

                duration = time.time() - start
                stdout = proc.stdout.read()
                stderr = proc.stderr.read()
                returncode = proc.returncode if proc.returncode is not None else 124
                if timed_out and returncode == 0:
                    returncode = 124

                if returncode == 0:
                    return {
                        "success": True,
                        "output": stdout,
                        "error": None,
                        "failure_type": None,
                        "duration_seconds": duration,
                        "retries": retries,
                    }
                else:
                    stderr_text = stderr or ""
                    if timed_out:
                        failure_type = "timeout"
                        if not stderr_text:
                            stderr_text = "Claude CLI timed out"
                    else:
                        failure_type = self._classify_failure(returncode, stderr_text)
                    last_failure_type = failure_type
                    last_error = stderr_text[:500]
                    last_duration = duration
                    self.logger.warning(
                        f"Claude CLI returned {returncode} ({failure_type}): {stderr_text[:200]}"
                    )
                    retries += 1
                    if attempt < self.max_retries:
                        delay = self._retry_delay(attempt)
                        self.logger.info(f"Retrying in {delay:.0f}s (attempt {attempt + 1})...")
                        time.sleep(delay)

            except FileNotFoundError:
                raise ClaudeCLIError(
                    f"Claude CLI not found at '{self.cli_command}'. "
                    "Install it or set claude_cli_command in config."
                )

        return {
            "success": False,
            "output": None,
            "error": f"Failed after {retries} retries"
            f"{': ' + last_error if last_error else ''}",
            "failure_type": last_failure_type or "transient",
            "duration_seconds": last_duration,
            "retries": retries,
        }

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
