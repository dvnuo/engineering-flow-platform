"""Tests for logger utilities."""

import logging
import pytest
from io import StringIO


@pytest.fixture(autouse=True)
def reset_root_logger():
    """Reset root logger after each test to avoid global state pollution."""
    # Save original handlers and level
    root = logging.getLogger()
    original_handlers = root.handlers.copy()
    original_level = root.level
    
    yield
    
    # Restore original state
    root.handlers = original_handlers
    root.setLevel(original_level)


from src.utils.logger import (
    EnhancedLogger,
    StructuredLogger,
    get_logger,
    get_structured_logger,
    setup_logging,
    LogContext,
    quick_setup,
    DEFAULT_FORMAT,
    STRUCTURED_FORMAT,
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

    def test_debug_method(self):
        """Test debug method."""
        logger = EnhancedLogger("test", logging.getLogger("test_debug"))
        logger.debug("debug message")

    def test_info_method(self):
        """Test info method."""
        logger = EnhancedLogger("test", logging.getLogger("test_info"))
        logger.info("info message")

    def test_warning_method(self):
        """Test warning method."""
        logger = EnhancedLogger("test", logging.getLogger("test_warning"))
        logger.warning("warning message")

    def test_error_method(self):
        """Test error method."""
        logger = EnhancedLogger("test", logging.getLogger("test_error"))
        logger.error("error message")

    def test_critical_method(self):
        """Test critical method."""
        logger = EnhancedLogger("test", logging.getLogger("test_critical"))
        logger.critical("critical message")

    def test_exception_method(self):
        """Test exception method."""
        logger = EnhancedLogger("test", logging.getLogger("test_exception"))
        try:
            raise ValueError("test")
        except ValueError:
            logger.exception("exception message")

    def test_log_call_without_args(self):
        """Test log_call without arguments."""
        logger = EnhancedLogger("test", logging.getLogger("test_log_call"))
        logger.log_call("test_function")

    def test_log_call_with_args(self):
        """Test log_call with arguments."""
        logger = EnhancedLogger("test", logging.getLogger("test_log_call"))
        logger.log_call("test_function", args=("a", "b"), kwargs={"key": "value"})

    def test_log_result_success(self):
        """Test log_result with success."""
        logger = EnhancedLogger("test", logging.getLogger("test_result"))
        logger.log_result("test_function", result="success")

    def test_log_result_error(self):
        """Test log_result with error."""
        logger = EnhancedLogger("test", logging.getLogger("test_result"))
        logger.log_result("test_function", error="failed")


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

    def test_structured_logger_warning(self, caplog):
        """Test structured logger warning level."""
        test_logger = logging.getLogger("test_structured_warn")
        structured = StructuredLogger("test_structured_warn", test_logger)
        
        with caplog.at_level(logging.WARNING):
            structured.warning("warning message")
        
        assert "warning message" in caplog.text

    def test_structured_logger_error(self, caplog):
        """Test structured logger error level."""
        test_logger = logging.getLogger("test_structured_err")
        structured = StructuredLogger("test_structured_err", test_logger)
        
        with caplog.at_level(logging.ERROR):
            structured.error("error message")
        
        assert "error message" in caplog.text

    def test_structured_logger_debug(self):
        """Test structured logger debug level."""
        test_logger = logging.getLogger("test_structured_debug")
        structured = StructuredLogger("test_structured_debug", test_logger)
        structured.debug("debug message")

    def test_structured_logger_critical(self):
        """Test structured logger critical level."""
        test_logger = logging.getLogger("test_structured_crit")
        structured = StructuredLogger("test_structured_crit", test_logger)
        structured.critical("critical message")


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

    def test_log_context_enter(self):
        """Test LogContext enter."""
        logger = EnhancedLogger("test")
        ctx = LogContext(logger, user_id="123")
        result = ctx.__enter__()
        assert result == ctx

    def test_log_context_exit(self):
        """Test LogContext exit."""
        logger = EnhancedLogger("test")
        ctx = LogContext(logger, user_id="123")
        ctx.__exit__(None, None, None)


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

    def test_setup_logging_debug_level(self, tmp_path):
        """Test logging with DEBUG level."""
        log_dir = tmp_path / "logs"
        logger = setup_logging(
            level="DEBUG",
            log_dir=str(log_dir),
            log_file="test.log",
            console=False,
        )
        assert logger.level == logging.DEBUG

    def test_setup_logging_warning_level(self, tmp_path):
        """Test logging with WARNING level."""
        log_dir = tmp_path / "logs"
        logger = setup_logging(
            level="WARNING",
            log_dir=str(log_dir),
            log_file="test.log",
            console=False,
        )
        assert logger.level == logging.WARNING


class TestQuickSetup:
    """Tests for quick_setup function."""

    def test_quick_setup_basic(self, tmp_path):
        """Test quick_setup basic."""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            logger = quick_setup("INFO")
            assert logger.level == logging.INFO
        finally:
            os.chdir(old_cwd)


class TestConstants:
    """Tests for module constants."""

    def test_default_format(self):
        """Test DEFAULT_FORMAT constant."""
        assert isinstance(DEFAULT_FORMAT, str)
        assert "%(asctime)s" in DEFAULT_FORMAT

    def test_structured_format(self):
        """Test STRUCTURED_FORMAT constant."""
        assert isinstance(STRUCTURED_FORMAT, str)
        assert "%(asctime)s" in STRUCTURED_FORMAT
