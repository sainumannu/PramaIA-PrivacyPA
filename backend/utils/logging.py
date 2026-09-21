"""
Logging configuration for PrivacyManager.

Provides a standardized logger with:
- Structured output (timestamp, level, module, function, message)
- File rotation (max 10MB per file, keep last 5 files)
- Console output with colors
- Configurable log level via LOG_LEVEL env var
- Request/Response logging middleware
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[32m',     # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[35m', # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record):
        # Add color to levelname
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logging(
    log_level: str = None,
    log_dir: str = "logs",
    log_file: str = "PramaIA-PrivacyManager.log",
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5
):
    """
    Setup application logging.
    
    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                   If None, reads from LOG_LEVEL env var (default: INFO)
        log_dir: Directory for log files (default: "logs")
        log_file: Log filename (default: "PramaIA-PrivacyManager.log")
        max_bytes: Max size per log file before rotation (default: 10MB)
        backup_count: Number of backup files to keep (default: 5)
    
    Returns:
        Configured logger instance
    """
    
    # Get log level from env or parameter
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Validate log level
    numeric_level = getattr(logging, log_level, logging.INFO)
    
    # Create logs directory
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Format strings
    detailed_format = (
        "%(asctime)s | %(levelname)-8s | %(name)s | "
        "%(funcName)s:%(lineno)d | %(message)s"
    )
    console_format = (
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        filename=log_path / log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setLevel(numeric_level)
    file_formatter = logging.Formatter(
        detailed_format,
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Windows default console encoding is cp1252 and cannot emit emoji
    # used in startup logs. Without UTF-8, logging.raiseExceptions dumps
    # a UnicodeEncodeError traceback that looks like a lifespan crash.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_formatter = ColoredFormatter(
        console_format,
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # Reduce verbosity of third-party loggers
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    app_logger = logging.getLogger("PramaIA-PrivacyManager")
    app_logger.info(f"Logging initialized: level={log_level}, file={log_path / log_file}")
    
    return app_logger


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance.
    
    Args:
        name: Logger name. If None, uses "PramaIA-PrivacyManager"
    
    Returns:
        Logger instance
    
    Example:
        ```python
        from backend.utils.logging import get_logger
        
        logger = get_logger(__name__)
        logger.info("Application started")
        logger.debug(f"User data: {user}")
        logger.error("Failed to connect to database", exc_info=True)
        ```
    """
    if name is None:
        name = "PramaIA-PrivacyManager"
    return logging.getLogger(name)


# Request/Response logging utilities

def sanitize_headers(headers: dict) -> dict:
    """
    Remove sensitive data from headers for logging.
    
    Args:
        headers: HTTP headers dict
        
    Returns:
        Sanitized headers dict
    """
    sensitive_keys = {
        "authorization", "cookie", "x-api-key", "x-app-secret",
        "x-auth-token", "set-cookie"
    }
    
    sanitized = {}
    for key, value in headers.items():
        key_lower = key.lower()
        if key_lower in sensitive_keys:
            sanitized[key] = "***REDACTED***"
        else:
            sanitized[key] = value
    
    return sanitized


def log_request(
    method: str,
    url: str,
    status_code: int = None,
    duration_ms: float = None,
    error: str = None
):
    """
    Log HTTP request details.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        url: Request URL
        status_code: Response status code
        duration_ms: Request duration in milliseconds
        error: Error message if request failed
    """
    logger = get_logger("http")
    
    if error:
        logger.error(f"{method} {url} | Error: {error}")
    elif status_code:
        level = logging.INFO if status_code < 400 else logging.ERROR
        duration_str = f" | {duration_ms:.0f}ms" if duration_ms else ""
        logger.log(level, f"{method} {url} | {status_code}{duration_str}")
    else:
        logger.debug(f"{method} {url}")
