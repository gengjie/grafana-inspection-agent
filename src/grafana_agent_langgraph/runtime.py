"""Shared runtime bootstrap helpers."""

from __future__ import annotations

import os
from pathlib import Path

from .config import AppConfig
from .logger import setup_logger


def resolve_config_path() -> Path | None:
    """Resolve config path from env and default candidates."""
    explicit = (
        os.getenv("GRAFANA_AGENT_CONFIG")
        or os.getenv("APP_CONFIG_PATH")
        or os.getenv("CONFIG_PATH")
    )
    if explicit:
        path = Path(explicit)
        return path if path.exists() else path

    candidates = [
        Path("config/config.yaml"),
        Path("config/config.example.yaml"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_config() -> AppConfig:
    """Load app config from YAML if available, otherwise environment variables."""
    config_path = resolve_config_path()
    if config_path and config_path.exists():
        return AppConfig.from_yaml(config_path)
    return AppConfig.from_env()


def init_logger_from_config(config: AppConfig):
    """Initialize logger using loaded configuration."""
    return setup_logger(
        level=config.logging.level,
        log_file=config.logging.log_file,
        log_format=config.logging.log_format,
        date_format=config.logging.date_format,
    )


def validate_base_config(config: AppConfig) -> None:
    """Validate required base config values."""
    if not config.grafana.url or not config.grafana.api_key:
        raise ValueError("Grafana URL and API key are required")


def validate_copilot_access_token(config: AppConfig) -> None:
    """Validate GitHub Copilot access token."""
    if not config.llm.access_token:
        raise ValueError("GitHub Copilot access token is required")
