"""Logging configuration module."""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logger(
    level: str = "INFO",
    log_file: Optional[str] = None,
    log_format: Optional[str] = None,
    date_format: Optional[str] = None,
) -> logging.Logger:
    """Setup and configure the application logger.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file (optional, if None, only console output)
        log_format: Custom log format string (optional)
        date_format: Custom date format string (optional)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger("grafana_agent")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Default format
    if log_format is None:
        # include filename and line number for easier debugging
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
    if date_format is None:
        date_format = "%Y-%m-%d %H:%M:%S"

    formatter = logging.Formatter(log_format, datefmt=date_format)

    # Console handler (always add - default output to terminal)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional - only if log_file is specified)
    # Note: If log_file is specified, logs will be written to both terminal and file
    if log_file:
        log_path = Path(log_file)
        # Create log directory if it doesn't exist
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger instance.

    Args:
        name: Logger name (optional, defaults to 'grafana_agent')

    Returns:
        Logger instance
    """
    if name:
        return logging.getLogger(f"grafana_agent.{name}")
    return logging.getLogger("grafana_agent")

