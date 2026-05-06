"""Tests for logger utilities."""

import logging
import io
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def reset_root_logger():
    """Reset root logger after each test to avoid global state pollution."""
    # Save original handlers and level
    root = logging.getLogger()
    original_handlers = root.handlers.copy()
    original_level = root.level
    
    yield
    
    # Close any new handlers created during the test
    for handler in root.handlers[:]:
        if handler not in original_handlers:
            handler.close()
            root.removeHandler(handler)
    
    # Restore original handlers and level
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
    RedactingFilter,
    RedactingFormatter,
    set_log_context,
    clear_log_context,
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

    def test_format_message_kwargs_control_characters_sanitized(self):
        """Context values should be safe for single-line logs."""
        logger = EnhancedLogger("test")
        result = logger._format_message("hello", user="abc\nx\r\ty")
        assert "\n" not in result
        assert "\r" not in result
        assert "\t" not in result
        assert "\\n" in result
        assert "\\r" in result
        assert "\\t" in result

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




    def test_setup_logging_attaches_redacting_filter(self, tmp_path):
        """All handlers should have redaction filter attached."""
        log_dir = tmp_path / "logs"
        logger = setup_logging(level="INFO", log_dir=str(log_dir), log_file="test.log", console=False)
        assert logger.handlers
        assert all(any(isinstance(f, RedactingFilter) for f in h.filters) for h in logger.handlers)


class TestRedactionIntegration:
    def test_log_output_is_redacted(self, capsys, tmp_path):
        setup_logging(level="INFO", log_dir=str(tmp_path / "logs"), log_file="test.log", console=True)
        test_logger = logging.getLogger("redact_integration")
        test_logger.info("Authorization: Bearer supersecret token=abc password=hunter2")

        captured = capsys.readouterr()
        out = captured.out
        assert "supersecret" not in out
        assert "hunter2" not in out
        assert "***REDACTED***" in out

    def test_default_format_includes_trace_fields_with_bound_context(self):
        stream = io.StringIO()
        logger = logging.getLogger("src.gateway.webchat")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)

        set_log_context(trace_id="trace-1", request_id="req-1", task_id="task-1", path="/api/tasks/execute")
        try:
            logger.info("hello")
        finally:
            clear_log_context()

        output = stream.getvalue()
        assert "trace=trace-1" in output
        assert "request=req-1" in output
        assert "task=task-1" in output
        assert "path=/api/tasks/execute" in output

    def test_default_format_omits_trace_block_without_context(self):
        stream = io.StringIO()
        logger = logging.getLogger("src.gateway.webchat")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)

        clear_log_context()
        logger.info("hello")
        output = stream.getvalue()
        assert "trace=" not in output
        assert "request=" not in output
        assert "path=" not in output

    def test_default_format_skips_trace_block_for_third_party_logger(self):
        stream = io.StringIO()
        logger = logging.getLogger("httpcore.connection")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)

        set_log_context(trace_id="trace-3", request_id="req-3", path="/x")
        try:
            logger.info("hello")
        finally:
            clear_log_context()

        output = stream.getvalue()
        assert "trace=" not in output

    def test_default_format_includes_trace_block_for_top_level_skill_logger(self):
        stream = io.StringIO()
        logger = logging.getLogger("skills.collect_requirements_to_bundle.skill")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)

        set_log_context(trace_id="trace-skill-1", request_id="req-skill-1", path="/api/tasks/execute")
        try:
            logger.info("skill started")
        finally:
            clear_log_context()

        output = stream.getvalue()
        assert "trace=trace-skill-1" in output
        assert "request=req-skill-1" in output
        assert "path=/api/tasks/execute" in output

    def test_exception_traceback_is_redacted(self):
        stream = io.StringIO()
        logger = logging.getLogger("redact_exception")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.ERROR)

        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactingFilter())
        handler.setFormatter(RedactingFormatter("%(levelname)s:%(message)s"))
        logger.addHandler(handler)

        try:
            raise ValueError("password=secret access_token=abc123")
        except ValueError:
            logger.exception("operation failed")

        output = stream.getvalue()
        assert "secret" not in output
        assert "abc123" not in output
        assert "***REDACTED***" in output

    def test_structured_log_args_are_redacted(self):
        stream = io.StringIO()
        logger = logging.getLogger("redact_structured_args")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)

        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactingFilter())
        handler.setFormatter(RedactingFormatter("%(message)s"))
        logger.addHandler(handler)

        logger.info("payload=%s", {"password": "secret", "nested": {"token": "abc123"}})
        output = stream.getvalue()
        assert "secret" not in output
        assert "abc123" not in output
        assert "***REDACTED***" in output

    def test_json_string_argument_is_redacted(self):
        stream = io.StringIO()
        logger = logging.getLogger("redact_json_arg")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)

        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactingFilter())
        handler.setFormatter(RedactingFormatter("%(message)s"))
        logger.addHandler(handler)

        logger.info("%s", '{"password": "secret"}')
        output = stream.getvalue()
        assert "secret" not in output
        assert "***REDACTED***" in output

    def test_filter_neutralizes_control_characters(self):
        stream = io.StringIO()
        logger = logging.getLogger("redact_ctrl_chars")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)

        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactingFilter())
        handler.setFormatter(RedactingFormatter("%(message)s"))
        logger.addHandler(handler)

        logger.info("payload=%s", "line1\npassword=secret\tline2\r")
        output = stream.getvalue()
        assert "\n" not in output.rstrip("\n")
        assert "\r" not in output
        assert "\t" not in output
        assert "\\n" in output
        assert "\\r" in output
        assert "\\t" in output
        assert "secret" not in output
        assert "***REDACTED***" in output

    def test_structured_logger_redacts_before_json_stringify(self):
        stream = io.StringIO()
        logger = logging.getLogger("structured_redaction")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)

        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactingFilter())
        handler.setFormatter(RedactingFormatter("%(message)s"))
        logger.addHandler(handler)

        structured = StructuredLogger("structured_redaction", logger)
        structured.info(
            "event",
            details={"openaiApiKey": "sk-secret", "nested": {"githubApiToken": "ghp_abc"}},
        )
        output = stream.getvalue()
        assert "sk-secret" not in output
        assert "ghp_abc" not in output
        assert "***REDACTED***" in output

    def test_malformed_formatting_fallback_is_safe(self):
        stream = io.StringIO()
        logger = logging.getLogger("redact_bad_format")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)

        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactingFilter())
        handler.setFormatter(RedactingFormatter("%(message)s"))
        logger.addHandler(handler)

        # Intentionally mismatched placeholders/args.
        logger.info("payload=%s %s", {"password": "secret", "token": "abc123"})
        output = stream.getvalue()
        assert "secret" not in output
        assert "abc123" not in output
        assert "***REDACTED***" in output

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
        assert "%(trace_block)s" in DEFAULT_FORMAT

    def test_structured_format(self):
        """Test STRUCTURED_FORMAT constant."""
        assert isinstance(STRUCTURED_FORMAT, str)
        assert "%(asctime)s" in STRUCTURED_FORMAT


def test_file_parser_modules_do_not_use_root_logging_calls_for_runtime_messages():
    repo_root = Path(__file__).resolve().parents[1]
    target_files = [
        repo_root / "src/utils/file_parser/pdf.py",
        repo_root / "src/utils/file_parser/image.py",
    ]
    forbidden_patterns = ("logging.warning(", "logging.error(", "logging.debug(")

    for file_path in target_files:
        content = file_path.read_text(encoding="utf-8")
        assert "logger = logging.getLogger(__name__)" in content
        for pattern in forbidden_patterns:
            assert pattern not in content


def test_default_format_includes_extended_runtime_context_fields():
    stream = io.StringIO()
    logger = logging.getLogger("src.runtime.execution_bus")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)

    set_log_context(
        trace_id="trace-1",
        request_id="req-1",
        session_id="sess-1",
        task_id="task-1",
        agent_id="agent-1",
        runtime_type="native",
        execution_type="tool",
        source_type="chat",
        tool_name="contract_echo",
        tool_source="external_tools_repo",
        skill_name="smoke-skill",
        profile_version="profile-v1",
        path="/api/chat",
    )
    try:
        logger.info("hello")
    finally:
        clear_log_context()

    output = stream.getvalue()
    assert "trace=trace-1" in output
    assert "request=req-1" in output
    assert "session=sess-1" in output
    assert "task=task-1" in output
    assert "agent=agent-1" in output
    assert "runtime=native" in output
    assert "exec=tool" in output
    assert "source=chat" in output
    assert "tool=contract_echo" in output
    assert "tool_source=external_tools_repo" in output
    assert "skill=smoke-skill" in output
    assert "profile=profile-v1" in output
    assert "path=/api/chat" in output


def test_extended_context_still_skips_third_party_logger():
    stream = io.StringIO()
    logger = logging.getLogger("httpcore.connection")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)

    set_log_context(request_id="req-3", runtime_type="native", tool_name="contract_echo")
    try:
        logger.info("hello")
    finally:
        clear_log_context()

    output = stream.getvalue()
    assert "request=req-3" not in output
    assert "runtime=native" not in output
    assert "tool=contract_echo" not in output
