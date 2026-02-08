"""Enhanced Logging Configuration for Engineering Flow Platform.

This module provides comprehensive logging setup with:
- Detailed format: timestamp, level, module, function, line, message
- Structured JSON logging option
- Module-specific loggers
- Exception traceback handling
- Log file rotation
"""

import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from typing import Optional, Dict, Any
import json
import traceback


# Custom log format with detailed info
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(funcName)s | %(message)s"

# Structured log format (JSON)
STRUCTURED_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(funcName)s | %(message)s"


class StructuredLogger:
    """Structured logger that outputs JSON logs."""
    
    def __init__(self, name: str, logger: logging.Logger):
        self.logger = logger
        self.name = name
    
    def _log_data(self, level: int, message: str, extra: Dict[str, Any] = None) -> None:
        """Create structured log entry."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": logging.getLevelName(level),
            "logger": self.name,
            "message": message,
            "extra": extra or {}
        }
        
        # Add exception info if available
        if extra and "exc_info" in extra and extra["exc_info"]:
            log_data["traceback"] = traceback.format_exc()
        
        self.logger.log(level, json.dumps(log_data))
    
    def debug(self, message: str, **extra) -> None:
        self._log_data(logging.DEBUG, message, extra)
    
    def info(self, message: str, **extra) -> None:
        self._log_data(logging.INFO, message, extra)
    
    def warning(self, message: str, **extra) -> None:
        self._log_data(logging.WARNING, message, extra)
    
    def error(self, message: str, exc_info: bool = True, **extra) -> None:
        extra = extra or {}
        extra["exc_info"] = exc_info
        self._log_data(logging.ERROR, message, extra)
    
    def critical(self, message: str, exc_info: bool = True, **extra) -> None:
        extra = extra or {}
        extra["exc_info"] = exc_info
        self._log_data(logging.CRITICAL, message, extra)


class EnhancedLogger:
    """Enhanced logger with detailed context tracking."""
    
    def __init__(self, name: str, logger: logging.Logger = None):
        self.name = name
        self.logger = logger or logging.getLogger(name)
    
    def _format_message(self, message: str, **kwargs) -> str:
        """Format message with context."""
        if kwargs:
            context = " | ".join(f"{k}={v}" for k, v in kwargs.items())
            return f"{message} | {context}"
        return message
    
    def debug(self, message: str, **kwargs) -> None:
        self.logger.debug(self._format_message(message, **kwargs))
    
    def info(self, message: str, **kwargs) -> None:
        self.logger.info(self._format_message(message, **kwargs))
    
    def warning(self, message: str, **kwargs) -> None:
        self.logger.warning(self._format_message(message, **kwargs))
    
    def error(self, message: str, exc_info: bool = True, **kwargs) -> None:
        if exc_info and sys.exc_info()[0]:
            tb = traceback.format_exc()
            kwargs["traceback"] = tb
        self.logger.error(self._format_message(message, **kwargs), exc_info=exc_info)
    
    def critical(self, message: str, exc_info: bool = True, **kwargs) -> None:
        if exc_info and sys.exc_info()[0]:
            tb = traceback.format_exc()
            kwargs["traceback"] = tb
        self.logger.critical(self._format_message(message, **kwargs), exc_info=exc_info)
    
    def exception(self, message: str, **kwargs) -> None:
        """Log exception with full traceback."""
        self.error(message, exc_info=True, **kwargs)
    
    def log_call(self, func_name: str, args: tuple = None, kwargs: dict = None):
        """Log function call with arguments."""
        msg = f"FUNC_CALL: {func_name}"
        if args:
            msg += f" | args={args}"
        if kwargs:
            msg += f" | kwargs={kwargs}"
        self.info(msg)
    
    def log_result(self, func_name: str, result: Any = None, error: str = None):
        """Log function result."""
        msg = f"FUNC_RESULT: {func_name}"
        if result is not None:
            msg += f" | result={result}"
        if error:
            msg += f" | error={error}"
        if error:
            self.error(msg)
        else:
            self.info(msg)


def setup_logging(
    level: str = "INFO",
    log_dir: str = "logs",
    log_file: str = "efp.log",
    max_size_mb: int = 10,
    backup_count: int = 5,
    structured: bool = False,
    console: bool = True
) -> logging.Logger:
    """Setup comprehensive logging configuration.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files
        log_file: Log file name
        max_size_mb: Maximum log file size in MB
        backup_count: Number of backup files to keep
        structured: Whether to use structured (JSON) logging
        console: Whether to output to console
    
    Returns:
        Root logger instance
    """
    # Convert level string to logging level
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Create logs directory
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Format
    log_format = STRUCTURED_FORMAT if structured else DEFAULT_FORMAT
    formatter = logging.Formatter(log_format)
    
    # Console handler
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # File handler with rotation
    log_path = Path(log_dir) / log_file
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # Error-only file handler (only ERROR and above)
    error_log_path = Path(log_dir) / "errors.log"
    error_handler = RotatingFileHandler(
        error_log_path,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)
    
    # Log startup info
    root_logger.info("=" * 60)
    root_logger.info("Engineering Flow Platform - Logging Initialized")
    root_logger.info(f"Log Level: {level}")
    root_logger.info(f"Log File: {log_path}")
    root_logger.info(f"Error Log: {error_log_path}")
    root_logger.info("=" * 60)
    
    return root_logger


def get_logger(name: str) -> EnhancedLogger:
    """Get an enhanced logger for a module.
    
    Args:
        name: Module name (typically __name__)
    
    Returns:
        EnhancedLogger instance
    """
    logger = logging.getLogger(name)
    return EnhancedLogger(name, logger)


def get_structured_logger(name: str) -> StructuredLogger:
    """Get a structured JSON logger for a module.
    
    Args:
        name: Module name (typically __name__)
    
    Returns:
        StructuredLogger instance
    """
    logger = logging.getLogger(name)
    return StructuredLogger(name, logger)


# Convenience function for quick logging setup
def quick_setup(level: str = "INFO") -> logging.Logger:
    """Quick logging setup with defaults.
    
    Args:
        level: Log level
    
    Returns:
        Root logger
    """
    return setup_logging(
        level=level,
        log_dir="logs",
        log_file="efp.log",
        max_size_mb=10,
        backup_count=5
    )


class LogContext:
    """Context manager for adding temporary context to logs."""
    
    def __init__(self, logger: EnhancedLogger, **context):
        self.logger = logger
        self.context = context
        self.old_context = {}
    
    def __enter__(self):
        # Add context to logger
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    
    def log(self, message: str, level: str = "info"):
        """Log with context."""
        full_message = self._format_message(message)
        log_method = getattr(self.logger, level.lower())
        log_method(full_message)
    
    def _format_message(self, message: str) -> str:
        if self.context:
            context_str = " | ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{message} | {context_str}"
        return message


# Example usage in modules:
"""
# At the top of each module:
from utils.logger import get_logger

logger = get_logger(__name__)

# In functions:
def my_function(param1: str, param2: int):
    logger.info(f"Starting function | param1={param1} | param2={param2}")
    
    try:
        result = do_something()
        logger.info(f"Function completed | result={result}")
        return result
    except Exception as e:
        logger.error(f"Function failed | error={e}", exc_info=True)
        raise

# Or using the enhanced logger:
def another_function():
    logger.log_call("another_function", args=("test",), kwargs={"key": "value"})
    
    # With context
    with LogContext(logger, user_id="123", action="update") as ctx:
        ctx.log("User performed action", level="info")
"""
