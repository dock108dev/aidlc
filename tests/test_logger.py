"""Tests for aidlc.logger module."""

import logging
import pytest
from pathlib import Path

from aidlc.logger import setup_logger, log_checkpoint


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
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(console_handlers) == 1
        assert console_handlers[0].level == logging.DEBUG

    def test_normal_mode(self, tmp_path):
        logger = setup_logger("test_normal", tmp_path, verbose=False)
        console_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
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
            "wall_clock_seconds": 8000,
            "phase": "implementing",
            "planning_cycles": 10,
            "issues_created": 5,
            "implementation_cycles": 3,
            "issues_implemented": 2,
            "issues_verified": 1,
        }
        log_checkpoint(logger, state_dict)
        content = (tmp_path / "cp_test.log").read_text()
        assert "CHECKPOINT" in content
        assert "implementing" in content
