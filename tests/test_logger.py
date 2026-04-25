"""Tests for aidlc.logger module."""

import logging

from aidlc.logger import log_checkpoint, setup_logger


class TestSetupLogger:
    def test_creates_log_files(self, tmp_path):
        logger = setup_logger("test_run", tmp_path, verbose=False)
        logger.info("Test message")
        logger.error("Error message")

        log_file = tmp_path / "test_run.log"
        error_file = tmp_path / "test_run.errors.log"
        assert log_file.exists()
        assert error_file.exists()

        log_content = log_file.read_text()
        assert "Test message" in log_content
        assert "Error message" in log_content

        error_content = error_file.read_text()
        assert "Error message" in error_content
        assert "Test message" not in error_content

    def test_verbose_mode(self, tmp_path):
        logger = setup_logger("test_verbose", tmp_path, verbose=True)
        # Check console handler is DEBUG level
        console_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(console_handlers) == 1
        assert console_handlers[0].level == logging.DEBUG

    def test_normal_mode(self, tmp_path):
        logger = setup_logger("test_normal", tmp_path, verbose=False)
        console_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(console_handlers) == 1
        assert console_handlers[0].level == logging.INFO

    def test_unique_logger_per_run(self, tmp_path):
        l1 = setup_logger("run1", tmp_path, verbose=False)
        l2 = setup_logger("run2", tmp_path, verbose=False)
        assert l1.name != l2.name


class TestLogCheckpoint:
    def test_logs_checkpoint(self, tmp_path):
        logger = setup_logger("cp_test", tmp_path)
        state_dict = {
            "elapsed_seconds": 7200,
            "console_seconds": 800,
            "phase": "implementing",
            "planning_cycles": 10,
            "issues_created": 5,
            "implementation_cycles": 3,
            "issues_implemented": 2,
            "issues_verified": 1,
            "claude_calls_total": 12,
            "claude_calls_succeeded": 10,
            "claude_calls_failed": 2,
            "claude_retries_total": 4,
            "claude_input_tokens": 1000,
            "claude_output_tokens": 400,
            "claude_cache_creation_input_tokens": 300,
            "claude_cache_read_input_tokens": 700,
            "claude_total_tokens": 2400,
            "claude_web_search_requests": 3,
            "claude_web_fetch_requests": 1,
            "claude_cost_usd_exact": 1.23,
            "claude_cost_usd_estimated": 1.11,
            "provider_account_usage": {
                "claude": {
                    "default": {
                        "calls": 8,
                        "calls_succeeded": 7,
                        "calls_failed": 1,
                        "input_tokens": 900,
                        "output_tokens": 300,
                        "total_tokens": 1200,
                        "cost_usd_exact": 1.0,
                        "cost_usd_estimated": 0.9,
                    }
                },
                "openai": {
                    "budget": {
                        "calls": 4,
                        "calls_succeeded": 3,
                        "calls_failed": 1,
                        "input_tokens": 100,
                        "output_tokens": 100,
                        "total_tokens": 200,
                        "cost_usd_exact": 0.23,
                        "cost_usd_estimated": 0.21,
                    }
                },
            },
        }
        log_checkpoint(logger, state_dict)
        content = (tmp_path / "cp_test.log").read_text()
        assert "CHECKPOINT" in content
        assert "implementing" in content
        assert "Provider calls" in content
        assert "Aggregate usage (all providers" in content
        assert "Per provider:" in content
        assert "claude/default:" in content
        assert "openai/budget:" in content
        assert "Provider cost (USD, all providers" in content

    def test_logs_checkpoint_no_provider_breakdown(self, tmp_path):
        logger = setup_logger("cp_empty", tmp_path)
        state_dict = {
            "elapsed_seconds": 0,
            "console_seconds": 0,
            "phase": "init",
            "planning_cycles": 0,
            "issues_created": 0,
            "implementation_cycles": 0,
            "issues_implemented": 0,
            "issues_verified": 0,
            "claude_calls_total": 0,
            "claude_calls_succeeded": 0,
            "claude_calls_failed": 0,
            "claude_retries_total": 0,
            "claude_input_tokens": 0,
            "claude_output_tokens": 0,
            "claude_cache_creation_input_tokens": 0,
            "claude_cache_read_input_tokens": 0,
            "claude_total_tokens": 0,
            "claude_web_search_requests": 0,
            "claude_web_fetch_requests": 0,
            "claude_cost_usd_exact": 0.0,
            "claude_cost_usd_estimated": 0.0,
            "provider_account_usage": {},
        }
        log_checkpoint(logger, state_dict)
        content = (tmp_path / "cp_empty.log").read_text()
        assert "Per provider: (no breakdown recorded)" in content
