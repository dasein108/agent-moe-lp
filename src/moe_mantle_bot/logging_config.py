"""
Centralized logging configuration for the Merchant Moe (Mantle) LP farming bot.
Provides consistent logging setup across all modules.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional


def setup_logging(
    log_level: Optional[str] = None,
    log_file: Optional[Path] = None,
    json_output: bool = False,
    debug_mode: bool = False
) -> logging.Logger:
    """
    Set up centralized logging configuration for the bot.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional file path for logging output
        json_output: If True, log in JSON format for structured logging
        debug_mode: If True, enable debug mode with verbose output
    
    Returns:
        Logger instance configured for the bot
    """
    
    # Determine log level
    if debug_mode:
        level = logging.DEBUG
    elif log_level:
        level = getattr(logging, log_level.upper(), logging.INFO)
    else:
        # Check environment variable
        env_level = os.getenv("MOE_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, env_level, logging.INFO)
    
    # Create formatter
    if json_output:
        # JSON formatter for structured logging
        formatter = JsonFormatter()
    else:
        # Human-readable formatter
        if debug_mode:
            # Detailed format for debugging
            format_string = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s"
        else:
            # Clean format for normal operation
            format_string = "%(asctime)s - %(levelname)s - %(message)s"
        
        formatter = logging.Formatter(
            format_string,
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    # Configure root logger
    logger = logging.getLogger("moe_mantle_bot")
    logger.setLevel(level)
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (optional)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    # Prevent duplicate logs in parent loggers
    logger.propagate = False
    
    return logger


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging output."""
    
    def format(self, record):
        import json
        from datetime import datetime
        
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields from LogRecord
        extra_fields = {
            key: value for key, value in record.__dict__.items()
            if key not in {
                'name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
                'filename', 'module', 'lineno', 'funcName', 'created',
                'msecs', 'relativeCreated', 'thread', 'threadName',
                'processName', 'process', 'stack_info', 'exc_info', 'exc_text'
            }
        }
        
        if extra_fields:
            log_entry.update(extra_fields)
        
        return json.dumps(log_entry, sort_keys=True)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module.
    
    Args:
        name: Logger name (typically __name__)
    
    Returns:
        Logger instance
    """
    return logging.getLogger(f"moe_mantle_bot.{name}")


# Convenience functions for different log levels
def log_info(message: str, logger_name: str = "main", **kwargs):
    """Log an info message."""
    logger = get_logger(logger_name)
    logger.info(message, extra=kwargs)


def log_warning(message: str, logger_name: str = "main", **kwargs):
    """Log a warning message."""
    logger = get_logger(logger_name)
    logger.warning(message, extra=kwargs)


def log_error(message: str, logger_name: str = "main", **kwargs):
    """Log an error message."""
    logger = get_logger(logger_name)
    logger.error(message, extra=kwargs)


def log_debug(message: str, logger_name: str = "main", **kwargs):
    """Log a debug message."""
    logger = get_logger(logger_name)
    logger.debug(message, extra=kwargs)


# Initialize default logger on import
_default_logger = None


def init_default_logger(debug: bool = False, json_output: bool = False):
    """Initialize the default logger for the bot."""
    global _default_logger
    
    log_file = None
    if os.getenv("MOE_LOG_FILE"):
        log_file = Path(os.getenv("MOE_LOG_FILE"))
    elif not json_output:
        # Default log file location
        log_file = Path("data/farm_bot.log")
    
    _default_logger = setup_logging(
        debug_mode=debug,
        json_output=json_output,
        log_file=log_file
    )
    
    return _default_logger


def get_default_logger() -> logging.Logger:
    """Get the default logger, initializing if necessary."""
    global _default_logger
    
    if _default_logger is None:
        init_default_logger()
    
    return _default_logger