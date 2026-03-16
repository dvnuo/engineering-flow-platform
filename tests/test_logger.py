"""Tests for logger utilities."""

import logging
import pytest
from io import StringIO

from src.utils.logger import (
    EnhancedLogger,
    StructuredLogger,
    get_logger,
    get_structured_logger,
    setup_logging,
    LogContext,
)


class TestEnhancedLogger:
    """Tests for EnhancedLogger class."""

    def test_enhanced_logger_basic(self):
        """Test basic logger creation."""
        logger = EnhancedLogger("test")
        assert logger.name == "test"

    def test_format_message_no_kwargs(self):
        """Test message formatting without kwargs."""
        logger = EnhancedLogger("test")
        result = logger._format_message("hello")
        assert result == "hello"

    def test_format_message_with_kwargs(self):
        """Test message formatting with kwargs."""
        logger = EnhancedLogger("test")
        result = logger._format_message("hello", user="test_user", action="login")
        assert "hello" in result
        assert "user=test_user" in result
        assert "action=login" in result

    def test_get_logger(self):
        """Test get_logger convenience function."""
        logger = get_logger("test_module")
        assert isinstance(logger, EnhancedLogger)
        assert logger.name == "test_module"


class TestStructuredLogger:
    """Tests for StructuredLogger class."""

    def test_structured_logger_basic(self):
        """Test basic structured logger creation."""
        logger = StructuredLogger("test", logging.getLogger("test"))
        assert logger.name == "test"

    def test_structured_logger_info(self, caplog):
        """Test structured logger info level."""
        test_logger = logging.getLogger("test_structured")
        structured = StructuredLogger("test_structured", test_logger)
        
        with caplog.at_level(logging.INFO):
            structured.info("test message", key="value")
        
        assert "test message" in caplog.text


class TestLogContext:
    """Tests for LogContext class."""

    def test_log_context_basic(self):
        """Test LogContext basic usage."""
        logger = EnhancedLogger("test")
        ctx = LogContext(logger, user_id="123", action="test")
        assert ctx.context == {"user_id": "123", "action": "test"}

    def test_log_context_format_message(self):
        """Test LogContext message formatting."""
        logger = EnhancedLogger("test")
        ctx = LogContext(logger, user_id="123")
        result = ctx._format_message("test message")
        assert "test message" in result
        assert "user_id=123" in result


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_logging_basic(self, tmp_path):
        """Test basic logging setup."""
        log_dir = tmp_path / "logs"
        logger = setup_logging(
            level="INFO",
            log_dir=str(log_dir),
            log_file="test.log",
            console=False,
        )
        assert logger.level == logging.INFO

    def test_setup_logging_creates_directory(self, tmp_path):
        """Test that logging creates log directory."""
        log_dir = tmp_path / "logs"
        setup_logging(
            level="INFO",
            log_dir=str(log_dir),
            log_file="test.log",
            console=False,
        )
        assert log_dir.exists()

    def test_setup_logging_creates_files(self, tmp_path):
        """Test that logging creates log files."""
        log_dir = tmp_path / "logs"
        setup_logging(
            level="INFO",
            log_dir=str(log_dir),
            log_file="test.log",
            console=False,
        )
        assert (log_dir / "test.log").exists()
        assert (log_dir / "errors.log").exists()

    def test_setup_logging_structured(self, tmp_path):
        """Test structured logging mode."""
        log_dir = tmp_path / "logs"
        logger = setup_logging(
            level="INFO",
            log_dir=str(log_dir),
            log_file="test.log",
            structured=True,
            console=False,
        )
        assert logger.level == logging.INFO
