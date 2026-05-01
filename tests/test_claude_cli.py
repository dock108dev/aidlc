"""Tests for aidlc.claude_cli module."""

import itertools
import json
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from aidlc.claude_cli import ClaudeCLI, ClaudeCLIError


@pytest.fixture
def logger():
    return logging.getLogger("test_claude_cli")


@pytest.fixture
def base_config():
    return {
        "providers": {"claude": {"cli_command": "claude", "default_model": "opus"}},
        "retry_max_attempts": 2,
        "retry_base_delay_seconds": 0.01,  # Fast for tests
        "retry_max_delay_seconds": 0.05,
        "retry_backoff_factor": 2.0,
        "claude_long_run_warn_seconds": 300,
        "dry_run": False,
    }


def _line_reader(text: str):
    """Return a readline-compatible callable that yields each line, then ''.

    Cycles back to the start after EOF so the same mock Popen can be reused
    across retries (each retry's reader thread gets a fresh stream).
    """
    lines = text.splitlines(keepends=True) if text else []
    queue = list(lines) + [""]
    idx = [0]

    def readline():
        if idx[0] >= len(queue):
            idx[0] = 0
        value = queue[idx[0]]
        idx[0] += 1
        return value

    return readline


def _mock_popen_success(stdout="output text", stderr=""):
    """Create a mock Popen that succeeds immediately."""
    proc = MagicMock()
    proc.poll.return_value = 0  # Process finished
    proc.wait.return_value = 0
    proc.returncode = 0
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline.side_effect = _line_reader(stdout)
    proc.stdout.read.return_value = stdout
    proc.stderr = MagicMock()
    proc.stderr.readline.side_effect = _line_reader(stderr)
    proc.stderr.read.return_value = stderr
    return proc


def _mock_popen_failure(returncode=1, stderr="error"):
    """Create a mock Popen that fails."""
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.wait.return_value = returncode
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline.side_effect = _line_reader("")
    proc.stdout.read.return_value = ""
    proc.stderr = MagicMock()
    proc.stderr.readline.side_effect = _line_reader(stderr)
    proc.stderr.read.return_value = stderr
    return proc


class TestClaudeCLIInit:
    def test_defaults(self, logger):
        cli = ClaudeCLI({}, logger)
        assert cli.cli_command == "claude"
        assert cli.model == "opus"
        assert cli.max_retries == 2
        assert cli.retry_base_delay == 30
        assert cli.retry_max_delay == 300
        assert cli.retry_backoff_factor == 2.0

    def test_custom_config(self, base_config, logger):
        cli = ClaudeCLI(base_config, logger)
        assert cli.retry_base_delay == 0.01
        assert cli.retry_max_delay == 0.05
        assert cli.retry_backoff_factor == 2.0


class TestRetryDelay:
    def test_exponential_growth(self, base_config, logger):
        cli = ClaudeCLI(base_config, logger)
        d0 = cli._retry_delay(0)
        d1 = cli._retry_delay(1)
        d2 = cli._retry_delay(2)
        assert d0 < d1 or d0 < 0.02
        assert d1 < d2 or d1 < 0.04

    def test_max_delay_cap(self, logger):
        config = {
            "retry_base_delay_seconds": 100,
            "retry_max_delay_seconds": 150,
            "retry_backoff_factor": 10.0,
        }
        cli = ClaudeCLI(config, logger)
        delay = cli._retry_delay(5)
        assert delay <= 150 * 1.25 + 1


class TestDryRun:
    def test_dry_run_returns_success(self, logger, tmp_path):
        config = {"dry_run": True}
        cli = ClaudeCLI(config, logger)
        result = cli.execute_prompt("test prompt", tmp_path)
        assert result["success"] is True
        assert result["output"] == "[DRY RUN] No execution"
        assert result["duration_seconds"] == 0.0
        assert result["retries"] == 0

    def test_dry_run_check_available(self, logger):
        config = {"dry_run": True}
        cli = ClaudeCLI(config, logger)
        assert cli.check_available() is True


class TestExecutePrompt:
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_success(self, mock_popen, base_config, logger, tmp_path):
        payload = {
            "result": "output text",
            "usage": {"input_tokens": 12, "output_tokens": 6},
        }
        mock_popen.return_value = _mock_popen_success(json.dumps(payload))
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is True
        assert result["output"] == "output text"
        assert result["failure_type"] is None
        assert result["usage"]["input_tokens"] == 12
        assert result["usage"]["output_tokens"] == 6

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_allow_edits_flag(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_success("ok")
        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path, allow_edits=True)
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_failure_retries(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_failure(1, "API error: rate limit")
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert result["retries"] == 3  # initial + 2 retries
        assert mock_popen.call_count == 3
        assert result["failure_type"] == "transient"

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_preserves_non_transient_failure_type(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_failure(1, "syntax error in prompt")
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert result["failure_type"] == "issue"
        assert "syntax error" in result["error"]

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_file_not_found_raises(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.side_effect = FileNotFoundError()
        cli = ClaudeCLI(base_config, logger)
        with pytest.raises(ClaudeCLIError, match="not found"):
            cli.execute_prompt("prompt", tmp_path)

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_model_override(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_success("ok")
        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path, model_override="sonnet")
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "sonnet"
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in cmd

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_session_id_flag(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_success("ok")
        cli = ClaudeCLI(base_config, logger)
        sid = "12345678-1234-5678-1234-567812345678"
        cli.execute_prompt("prompt", tmp_path, continuation_session_id=sid)
        cmd = mock_popen.call_args[0][0]
        assert "--session-id" in cmd
        assert cmd[cmd.index("--session-id") + 1] == sid

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_extracts_cost_model_and_tool_usage(self, mock_popen, base_config, logger, tmp_path):
        payload = {
            "result": "done",
            "model": "claude-sonnet-4-6",
            "total_cost_usd": 0.1234,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_creation_input_tokens": 40,
                "cache_read_input_tokens": 200,
                "server_tool_use": {
                    "web_search_requests": 2,
                    "web_fetch_requests": 1,
                },
            },
        }
        mock_popen.return_value = _mock_popen_success(json.dumps(payload))
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["total_cost_usd"] == pytest.approx(0.1234)
        assert result["model_used"] == "claude-sonnet-4-6"
        assert result["usage"]["cache_creation_input_tokens"] == 40
        assert result["usage"]["cache_read_input_tokens"] == 200
        assert result["usage"]["web_search_requests"] == 2
        assert result["usage"]["web_fetch_requests"] == 1

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_falls_back_to_raw_output_when_json_parse_fails(
        self, mock_popen, base_config, logger, tmp_path
    ):
        mock_popen.return_value = _mock_popen_success("non-json response")
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["output"] == "non-json response"
        assert result["usage"] == {}
        assert result["total_cost_usd"] is None

    @patch("aidlc.claude_cli.time.sleep")
    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_service_outage_retries_until_window_expires(
        self, mock_popen, mock_time, mock_sleep, base_config, logger, tmp_path
    ):
        mock_popen.return_value = _mock_popen_failure(1, "HTTP 500 internal server error")
        base_config["retry_max_attempts"] = 0
        base_config["claude_service_outage_max_wait_seconds"] = 3
        clock = itertools.count(start=0.0, step=1.0)
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert result["failure_type"] == "service_down"
        assert "unavailable for an extended period" in result["error"]
        assert mock_popen.call_count > 1
        assert mock_sleep.called

    @patch("aidlc.claude_cli.time.sleep")
    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_outage_classification_is_sticky_across_retry(
        self, mock_popen, mock_time, mock_sleep, base_config, logger, tmp_path
    ):
        # Reproduces the logs_4 mid-outage misclassification:
        #   call 1: rc=1 with explicit "internal server error" markers   -> service_down
        #   call 2+: rc=1 with plain stdout that the regex would miss    -> WAS transient
        # With Fix A (sticky window), all post-call-1 failures stay
        # service_down for the duration of the outage budget. Use a small
        # budget so the loop ends via budget-exhaustion, not retry exhaustion.
        first = _mock_popen_failure(1, "")
        first.stdout.read.return_value = "HTTP 500 internal server error\nupstream connect failure"
        first.stdout.readline.side_effect = _line_reader(
            "HTTP 500 internal server error\nupstream connect failure\n"
        )
        second = _mock_popen_failure(1, "")
        second.stdout.read.return_value = "..."
        second.stdout.readline.side_effect = _line_reader("...\n")

        # First call uses `first`, every subsequent call uses `second`.
        # A generator lets the loop run as many iterations as it wants
        # without exhausting a fixed-size side_effect list.
        def popen_factory():
            yield first
            while True:
                yield second

        gen = popen_factory()
        mock_popen.side_effect = lambda *a, **kw: next(gen)

        # Budget = 30 mocked seconds (a handful of outage retries given the
        # wait loop's internal time.time() calls). Plenty to prove stickiness,
        # finite enough to hit budget exhaustion and exit cleanly.
        base_config["retry_max_attempts"] = 0
        base_config["claude_service_outage_max_wait_seconds"] = 30
        clock = itertools.count(start=0.0, step=1.0)
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        # Final result is service_down (budget exhausted). Without Fix A
        # the classifier would have returned to "issue"/"transient" on
        # call 2 and exited via the regular max_retries path with that
        # classification.
        assert result["success"] is False
        assert result["failure_type"] == "service_down"
        # And it must have retried more than the regular max_retries=0+1=1
        # default — proves we stayed in the outage retry path.
        assert mock_popen.call_count >= 2

    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_hard_timeout_config_is_ignored(
        self, mock_popen, mock_time, base_config, logger, tmp_path
    ):
        """SSOT: ``claude_hard_timeout_seconds`` was removed entirely.
        Setting it (legacy config files may still carry it) must NOT
        cause Claude CLI to be killed on wall-clock alone — productive
        multi-hour streaming sessions used to be interrupted, leaving
        partial JSON that downstream parsers couldn't handle. Activity-
        based stall detection is the only kill path."""
        proc = MagicMock()
        proc.poll.side_effect = [None, None, 0]  # finishes naturally
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            0,
        ]
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.readline.side_effect = _line_reader("")
        proc.stderr = MagicMock()
        proc.stderr.readline.side_effect = _line_reader("")
        mock_popen.return_value = proc

        # Legacy config that USED to trigger a kill at 1s wall-clock.
        base_config["claude_hard_timeout_seconds"] = 1
        base_config["retry_max_attempts"] = 0
        clock = itertools.count(start=0.0, step=2.0)
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path)
        # No kill signal sent: the legacy hard_timeout has no effect.
        assert not proc.send_signal.called
        assert not proc.terminate.called


class TestExtractCliMetadataNdjson:
    def test_picks_jsonl_line_with_usage_when_stdout_not_single_json(self):
        line1 = json.dumps({"type": "progress", "note": "x"})
        line2 = json.dumps(
            {
                "result": "from line",
                "usage": {"input_tokens": 5, "output_tokens": 3},
                "model": "sonnet",
            }
        )
        blob = f"noise prefix\n{line1}\n{line2}\n"
        text, usage, cost, model, src = ClaudeCLI._extract_cli_metadata(blob, "opus")
        assert text == "from line"
        assert usage["input_tokens"] == 5
        assert usage["output_tokens"] == 3
        assert model == "sonnet"
        assert src == "claude_cli_json"
        assert cost is None


class TestStreamJsonAssembly:
    """stream-json output reassembles into the same fields the parser expects."""

    def test_terminal_result_event_yields_expected_metadata(self):
        # Minimal stream-json transcript as Claude CLI would emit it.
        events = [
            {"type": "system", "subtype": "init", "tools": ["Read"]},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hi"}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "final text",
                "total_cost_usd": 0.42,
                "model": "sonnet",
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 5,
                },
            },
        ]
        blob = "\n".join(json.dumps(e) for e in events) + "\n"
        text, usage, cost, model, src = ClaudeCLI._extract_cli_metadata(blob, "opus")
        assert text == "final text"
        assert usage["input_tokens"] == 11
        assert usage["output_tokens"] == 7
        assert usage["cache_read_input_tokens"] == 5
        assert cost == 0.42
        assert model == "sonnet"
        assert src == "claude_cli_json"

    def test_command_line_uses_stream_json_and_verbose(self, base_config, logger, tmp_path):
        with patch("aidlc.claude_cli.subprocess.Popen") as mock_popen:
            mock_popen.return_value = _mock_popen_success("{}")
            cli = ClaudeCLI(base_config, logger)
            cli.execute_prompt("prompt", tmp_path)
            cmd = mock_popen.call_args[0][0]
            assert "--output-format" in cmd
            assert cmd[cmd.index("--output-format") + 1] == "stream-json"
            assert "--verbose" in cmd


class TestSummarizeStreamEvent:
    def test_tool_use_with_path(self):
        from aidlc.claude_cli import _summarize_stream_event

        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "src/foo.py"},
                        }
                    ]
                },
            }
        )
        assert _summarize_stream_event(line) == "tool_use Read(src/foo.py)"

    def test_assistant_text_counts_chars(self):
        from aidlc.claude_cli import _summarize_stream_event

        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hello world"}]},
            }
        )
        assert _summarize_stream_event(line) == "assistant_text 11 chars"

    def test_result_event(self):
        from aidlc.claude_cli import _summarize_stream_event

        line = json.dumps({"type": "result", "subtype": "success", "is_error": False})
        assert _summarize_stream_event(line) == "result success"

    def test_garbage_returns_empty_summary(self):
        from aidlc.claude_cli import _summarize_stream_event

        assert _summarize_stream_event("not json") == ""
        assert _summarize_stream_event("") == ""
        assert _summarize_stream_event("{") == ""


class TestLivenessLoop:
    """Stall-based heartbeat — wall-clock kills were removed entirely."""

    def test_hard_timeout_config_key_is_absent_from_defaults(self):
        """SSOT: ``claude_hard_timeout_seconds`` was removed from DEFAULTS.
        Reintroducing it would resurrect the wall-clock kill that
        interrupted productive multi-hour Claude sessions."""
        from aidlc.config import DEFAULTS

        assert "claude_hard_timeout_seconds" not in DEFAULTS

    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_stall_kill_requests_graceful_stop_on_silence(
        self, mock_popen, mock_time, base_config, logger, caplog, tmp_path
    ):
        """When stall_kill_seconds is set and no stream activity occurs, the
        CLI is asked to stop (send_signal), and the stall_kill path is logged."""
        proc = MagicMock()
        proc.poll.side_effect = [None, 124]
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            0,  # graceful stop succeeds
        ]
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.readline.side_effect = _line_reader("")
        proc.stderr = MagicMock()
        proc.stderr.readline.side_effect = _line_reader("")
        mock_popen.return_value = proc

        base_config["claude_stall_kill_seconds"] = 1
        base_config["retry_max_attempts"] = 0
        # time advances fast enough that the stall_kill threshold trips.
        clock = itertools.count(start=0.0, step=5.0)
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        with caplog.at_level(logging.WARNING, logger="test_claude_cli"):
            cli.execute_prompt("prompt", tmp_path)

        # Stall-kill path asked for a graceful stop (send_signal -> SIGINT).
        assert proc.send_signal.called
        # The kill reason was logged so an operator can see why.
        assert any("stall kill" in rec.message for rec in caplog.records)

    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_post_terminal_idle_kills_hung_cli_after_result_event(
        self, mock_popen, mock_time, base_config, logger, caplog, tmp_path
    ):
        """Regression for the user's stuck-on-result-success log: after the
        model emits the terminal stream-json `result success` event, the
        CLI should exit. When it hangs (CLI bug, OS buffer, etc.), the
        wrapper kills it after ``claude_post_terminal_idle_seconds`` of
        further silence — independent of the broader ``claude_stall_kill_seconds``
        runaway threshold. This protects against post-terminal hangs
        without false-positive killing during long real tool runs."""
        terminal_event = '{"type": "result", "subtype": "success"}\n'
        proc = MagicMock()
        proc.poll.side_effect = [None, 0]
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            0,
        ]
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        # Emit the terminal event once, then EOF — simulates "model done,
        # CLI hangs."
        proc.stdout.readline.side_effect = _line_reader(terminal_event)
        proc.stderr = MagicMock()
        proc.stderr.readline.side_effect = _line_reader("")
        mock_popen.return_value = proc

        # Tight post-terminal threshold for the test; default is 30s.
        base_config["claude_post_terminal_idle_seconds"] = 1
        base_config["claude_stall_kill_seconds"] = 0  # not the path we're testing
        base_config["retry_max_attempts"] = 0
        # Each tick advances 5s; idle past the 1s threshold fires immediately.
        clock = itertools.count(start=0.0, step=5.0)
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        with caplog.at_level(logging.INFO, logger="test_claude_cli"):
            cli.execute_prompt("prompt", tmp_path)

        # Graceful stop was requested (post-terminal hang detected).
        assert proc.send_signal.called
        # The kill reason and the terminal-event detection were both logged.
        assert any("post-terminal hang" in rec.message for rec in caplog.records)
        assert any("emitted terminal" in rec.message for rec in caplog.records)

    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_post_terminal_idle_disabled_when_zero(
        self, mock_popen, mock_time, base_config, logger, caplog, tmp_path
    ):
        """``claude_post_terminal_idle_seconds=0`` disables the post-terminal
        kill — useful when running with a debugger attached or when a user
        has external lifecycle management."""
        terminal_event = '{"type": "result", "subtype": "success"}\n'
        proc = MagicMock()
        # Process exits naturally on the third poll; without the kill path
        # firing, the wrapper just waits.
        proc.poll.side_effect = [None, None, None, 0]
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            0,
        ]
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.readline.side_effect = _line_reader(terminal_event)
        proc.stderr = MagicMock()
        proc.stderr.readline.side_effect = _line_reader("")
        mock_popen.return_value = proc

        base_config["claude_post_terminal_idle_seconds"] = 0  # disabled
        base_config["claude_stall_kill_seconds"] = 0
        base_config["retry_max_attempts"] = 0
        clock = itertools.count(start=0.0, step=60.0)  # huge idle gaps
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path)
        # No kill happened — process exited naturally.
        assert not proc.send_signal.called
        assert not proc.terminate.called

    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_post_terminal_idle_does_not_fire_during_real_work(
        self, mock_popen, mock_time, base_config, logger, tmp_path
    ):
        """During a long-running tool call (e.g. a 5-minute Bash test), idle
        can legitimately spike to several minutes. The post-terminal kill
        must NOT fire because no terminal `result` event has been emitted
        yet. Stall-kill is the only path that could trigger here, and it's
        disabled by default."""
        # Stream events that look like an in-progress run: tool_use, tool_result,
        # but no terminal `result` event.
        in_progress = (
            '{"type": "assistant", "message": {"content": [{"type": "tool_use", '
            '"name": "Bash", "input": {"command": "long-test"}}]}}\n'
        )
        proc = MagicMock()
        proc.poll.side_effect = [None, None, 0]
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            0,
        ]
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.readline.side_effect = _line_reader(in_progress)
        proc.stderr = MagicMock()
        proc.stderr.readline.side_effect = _line_reader("")
        mock_popen.return_value = proc

        base_config["claude_post_terminal_idle_seconds"] = 30
        base_config["claude_stall_kill_seconds"] = 0
        base_config["retry_max_attempts"] = 0
        # Huge idle gaps to prove post-terminal does NOT fire without the
        # terminal event.
        clock = itertools.count(start=0.0, step=300.0)
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path)
        # No kill — no terminal event was emitted, so post-terminal is inert.
        assert not proc.send_signal.called

    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_no_kill_on_elapsed_alone(self, mock_popen, mock_time, base_config, logger, tmp_path):
        """Wall-clock kill removed: no matter how long Claude runs, only
        activity-based stall_kill can interrupt it."""
        proc = MagicMock()
        # Process exits naturally on the 3rd poll.
        proc.poll.side_effect = [None, None, 0]
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            subprocess.TimeoutExpired(cmd="claude", timeout=1.0),
            0,
        ]
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.readline.side_effect = _line_reader("{}")
        proc.stderr = MagicMock()
        proc.stderr.readline.side_effect = _line_reader("")
        mock_popen.return_value = proc

        base_config["claude_stall_kill_seconds"] = 0
        base_config["retry_max_attempts"] = 0
        # Advance time by an hour each tick — no kill should happen.
        clock = itertools.count(start=0.0, step=3600.0)
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path)
        assert not proc.send_signal.called
        assert not proc.terminate.called
        assert not proc.kill.called


class TestClassifyFailure:
    def test_transient_rate_limit(self):
        assert ClaudeCLI._classify_failure(1, "rate limit exceeded") == "transient"

    def test_transient_503(self):
        assert ClaudeCLI._classify_failure(1, "error 503 service unavailable") == "transient"

    def test_transient_connection_error(self):
        assert ClaudeCLI._classify_failure(1, "connection refused") == "transient"

    def test_non_transient_issue(self):
        assert ClaudeCLI._classify_failure(1, "syntax error") == "issue"

    def test_signal_death_is_transient(self):
        assert ClaudeCLI._classify_failure(137, "") == "transient"
        assert ClaudeCLI._classify_failure(-9, "") == "transient"

    def test_service_outage_detection_from_500(self):
        assert ClaudeCLI._is_service_outage(1, "HTTP 500 internal server error", "") is True

    def test_service_outage_detection_from_network_message(self):
        assert ClaudeCLI._is_service_outage(1, "", "temporary DNS failure") is True

    def test_service_outage_detection_from_init_only_stdout(self):
        # Real outage fingerprint observed in logs_4: CLI emits the system
        # init event but never produces a result event, then exits non-zero.
        init_only = (
            '{"type":"system","subtype":"init","cwd":"/Users/x/proj",'
            '"session_id":"abcd","tools":["Task","Bash","Edit"],'
            '"model":"opus"}\n'
        )
        assert ClaudeCLI._is_service_outage(1, "", init_only) is True

    def test_init_plus_result_is_not_outage(self):
        # If the CLI got past init and produced a result event, the failure
        # is about the run itself (test failure, etc.) — not an outage.
        init_then_result = (
            '{"type":"system","subtype":"init","session_id":"abcd"}\n'
            '{"type":"result","subtype":"error","error":"tests failed"}\n'
        )
        assert ClaudeCLI._is_service_outage(1, "", init_then_result) is False

    def test_init_only_stdout_with_zero_returncode_is_not_outage(self):
        # rc=0 means the success branch handles parsing; outage detection
        # should not fire.
        init_only = '{"type":"system","subtype":"init","session_id":"abcd"}\n'
        assert ClaudeCLI._is_service_outage(0, "", init_only) is False


class TestExtractCliMetadataBranches:
    def test_whitespace_only_stdout(self):
        text, usage, cost, model, src = ClaudeCLI._extract_cli_metadata("  \n  ", "fb")
        assert src == "none"
        assert usage == {}

    def test_message_dict_usage_when_top_usage_missing(self):
        payload = {
            "message": {
                "usage": {"input_tokens": 2, "output_tokens": 1},
                "content": [{"type": "text", "text": "hello"}],
                "model": "opus-2",
                "total_cost_usd": "0.01",
            }
        }
        blob = json.dumps(payload)
        text, usage, cost, model, src = ClaudeCLI._extract_cli_metadata(blob, "fb")
        assert text == "hello"
        assert usage.get("input_tokens") == 2
        assert model == "opus-2"
        assert cost == 0.01
        assert src == "claude_cli_json"

    def test_non_dict_top_level_scans_json_lines(self):
        blob = "prefix\n" + json.dumps({"usage": {"output_tokens": 7}, "result": "r2"})
        text, usage, _, model, src = ClaudeCLI._extract_cli_metadata(blob, "fb")
        assert text == "r2"
        assert usage["output_tokens"] == 7
        assert src == "claude_cli_json"
        assert model == "fb"

    def test_usage_with_server_tool_use_dict(self):
        payload = {
            "result": "ok",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "server_tool_use": {"web_search_requests": 2, "web_fetch_requests": 3},
            },
            "model": "m",
        }
        text, usage, _, _, src = ClaudeCLI._extract_cli_metadata(json.dumps(payload), "fb")
        assert text == "ok"
        assert usage["web_search_requests"] == 2
        assert usage["web_fetch_requests"] == 3
        assert src == "claude_cli_json"

    def test_invalid_total_cost_and_model_ignored(self):
        payload = {"result": "x", "usage": {}, "total_cost_usd": "nope", "model": ""}
        _, _, cost, model, _ = ClaudeCLI._extract_cli_metadata(json.dumps(payload), "fb")
        assert cost is None
        assert model == "fb"

    def test_extract_text_from_message_non_list_content(self):
        assert ClaudeCLI._extract_text_from_message({"content": "bad"}) == ""

    def test_extract_text_from_message_skips_non_text_blocks(self):
        msg = {
            "content": [
                {"type": "tool_use", "name": "x"},
                {"type": "text", "text": "only"},
            ]
        }
        assert ClaudeCLI._extract_text_from_message(msg) == "only"
