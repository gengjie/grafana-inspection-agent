"""Configuration management module."""

import os
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environment variable → nested config path mapping with type converter.
_ENV_OVERRIDES: list[tuple[str, list[str], Callable[[str], Any]]] = [
    ("GRAFANA_URL", ["grafana", "url"], str),
    ("GRAFANA_API_KEY", ["grafana", "api_key"], str),
    ("GRAFANA_TIMEOUT", ["grafana", "timeout"], int),
    ("GRAFANA_VERIFY_SSL", ["grafana", "verify_ssl"], lambda v: v.lower() == "true"),
    ("GRAFANA_CA_FILE", ["grafana", "ca_file"], str),
    (
        "GRAFANA_SLOW_QUERY_DASHBOARD_UIDS",
        ["grafana", "slow_query_dashboard_uids"],
        lambda v: [uid.strip() for uid in v.split(",") if uid.strip()],
    ),
    (
        "GRAFANA_SLOW_QUERY_DASHBOARD_UID",
        ["grafana", "slow_query_dashboard_uids"],
        lambda v: [v.strip()] if v.strip() else [],
    ),
    ("LLM_PROVIDER", ["llm", "provider"], str),
    ("COPILOT_ACCESS_TOKEN", ["llm", "access_token"], str),
    ("LLM_MODEL", ["llm", "model"], str),
    ("COPILOT_API_BASE", ["llm", "api_base"], str),
    ("COPILOT_TOKEN_URL", ["llm", "token_url"], str),
    ("COPILOT_EDITOR_VERSION", ["llm", "editor_version"], str),
    ("COPILOT_EDITOR_PLUGIN_VERSION", ["llm", "editor_plugin_version"], str),
    ("COPILOT_USER_AGENT", ["llm", "user_agent"], str),
    ("LLM_TEMPERATURE", ["llm", "temperature"], float),
    ("LLM_MAX_TOKENS", ["llm", "max_tokens"], int),
    ("LLM_REQUEST_TIMEOUT", ["llm", "request_timeout"], int),
    ("LLM_CHUNK_MAX_RETRIES", ["llm", "chunk_max_retries"], int),
    ("LLM_CHUNK_RETRY_BACKOFF_SECONDS", ["llm", "chunk_retry_backoff_seconds"], float),
    ("LLM_CHUNK_RETRY_MAX_BACKOFF_SECONDS", ["llm", "chunk_retry_max_backoff_seconds"], float),
    ("LLM_JVM_MAX_PANELS", ["llm", "jvm_max_panels"], int),
    (
        "LLM_JVM_KEYWORDS",
        ["llm", "jvm_keywords"],
        lambda v: [kw.strip() for kw in v.split(",") if kw.strip()],
    ),
    ("LOG_LEVEL", ["logging", "level"], str),
    ("SMTP_HOST", ["notification", "email", "smtp_host"], str),
    ("SMTP_PORT", ["notification", "email", "smtp_port"], int),
    ("SMTP_USER", ["notification", "email", "smtp_user"], str),
    ("SMTP_PASSWORD", ["notification", "email", "smtp_password"], str),
    ("EMAIL_FROM", ["notification", "email", "from_address"], str),
    ("EMAIL_ENABLED", ["notification", "email", "enabled"], lambda v: v.lower() == "true"),
    ("EMAIL_TO", ["notification", "email", "to_addresses"], lambda v: [a.strip() for a in v.split(",") if a.strip()]),
    ("TEAMS_ENABLED", ["notification", "teams", "enabled"], lambda v: v.lower() == "true"),
    ("TEAMS_WEBHOOK_URL", ["notification", "teams", "webhook_url"], str),
    ("TIMEZONE", ["timezone"], str),
    ("LOOKBACK_HOURS", ["lookback_hours"], int),
    ("LANGUAGE", ["language"], str),
]


class GrafanaConfig(BaseModel):
    """Grafana connection configuration."""

    url: str = Field(..., description="Grafana instance URL")
    api_key: str = Field(..., description="Grafana API key")
    timeout: int = Field(default=30, description="Request timeout in seconds")
    verify_ssl: bool = Field(default=False, description="Whether to verify TLS certificates")
    ca_file: Optional[str] = Field(
        default=None,
        description="Custom CA bundle path used to verify Grafana TLS certificate",
    )
    slow_query_dashboard_uids: list[str] = Field(
        default_factory=lambda: ["aawp84s"],
        description="Dashboard UIDs used for dedicated slow-query SQL diagnosis",
    )

    @field_validator("ca_file")
    @classmethod
    def normalize_ca_file(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = v.strip()
        if not value:
            return None
        return str(Path(value).expanduser())

    @field_validator("slow_query_dashboard_uids", mode="before")
    @classmethod
    def validate_slow_query_dashboard_uids(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = [item.strip() for item in v.split(",") if item.strip()]

        if not isinstance(v, list):
            raise ValueError("slow_query_dashboard_uids must be a list")

        cleaned = [str(item).strip() for item in v if str(item).strip()]
        if not cleaned:
            raise ValueError("slow_query_dashboard_uids cannot be empty")
        return cleaned


class LLMConfig(BaseModel):
    """LLM API configuration."""

    provider: str = Field(default="github_copilot", description="LLM provider, must be github_copilot")
    access_token: str = Field(..., description="GitHub access token for exchanging Copilot session token")
    model: str = Field(default="claude-sonnet-4.6", description="Copilot model name")
    api_base: str = Field(default="https://api.githubcopilot.com", description="Copilot API base URL")
    token_url: str = Field(
        default="https://api.github.com/copilot_internal/v2/token",
        description="GitHub endpoint for Copilot session token exchange",
    )
    editor_version: str = Field(default="vscode/1.99.0", description="Editor version header")
    editor_plugin_version: str = Field(
        default="copilot-chat/0.26.7",
        description="Copilot plugin version header",
    )
    user_agent: str = Field(default="GitHubCopilotChat/0.26.7", description="HTTP User-Agent")
    temperature: float = Field(default=0.3, description="Temperature for generation")
    max_tokens: int = Field(default=2000, description="Maximum tokens for generation")
    request_timeout: int = Field(default=180, description="Copilot request timeout in seconds")
    chunk_max_retries: int = Field(
        default=2,
        description="Retry times for chunk tasks when transient errors occur",
    )
    chunk_retry_backoff_seconds: float = Field(
        default=1.0,
        description="Base backoff seconds between chunk retries",
    )
    chunk_retry_max_backoff_seconds: float = Field(
        default=8.0,
        description="Maximum backoff seconds between chunk retries",
    )
    jvm_max_panels: int = Field(
        default=100,
        description="Maximum JVM-related panels to include in one run",
    )
    jvm_keywords: list[str] = Field(
        default_factory=lambda: [
            "jvm", "heap", "non-heap", "nonheap", "gc", "garbage",
            "eden", "survivor", "old gen", "tenured", "metaspace",
            "codecache", "code cache", "thread", "class loading",
            "jvm_memory", "jvm_gc", "jvm_threads", "jvm_buffer",
            "process_cpu", "hikari", "tomcat", "java_lang",
            "direct_buffer", "mapped_buffer", "g1", "young gen",
        ],
        description="Keyword list for JVM panel matching",
    )

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Only GitHub Copilot is supported."""
        provider = (v or "").strip().lower()
        if provider not in {"github_copilot", "copilot"}:
            raise ValueError("Only github_copilot provider is supported")
        return "github_copilot"

    @field_validator("jvm_max_panels")
    @classmethod
    def validate_jvm_max_panels(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("jvm_max_panels must be > 0")
        return v

    @field_validator("request_timeout")
    @classmethod
    def validate_request_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("request_timeout must be > 0")
        return v

    @field_validator("chunk_max_retries")
    @classmethod
    def validate_chunk_max_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError("chunk_max_retries must be >= 0")
        return v

    @field_validator("chunk_retry_backoff_seconds", "chunk_retry_max_backoff_seconds")
    @classmethod
    def validate_chunk_retry_backoff(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("chunk retry backoff must be > 0")
        return v

    @field_validator("jvm_keywords")
    @classmethod
    def validate_jvm_keywords(cls, v: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in (v or []) if str(item).strip()]
        if not cleaned:
            raise ValueError("jvm_keywords cannot be empty")
        return cleaned


class EmailConfig(BaseModel):
    """Email notification configuration."""

    enabled: bool = Field(default=False, description="Enable email notifications")
    smtp_host: str = Field(default="", description="SMTP server host")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: str = Field(default="", description="SMTP username")
    smtp_password: str = Field(default="", description="SMTP password")
    from_address: str = Field(default="", description="Sender email address")
    to_addresses: list[str] = Field(default_factory=list, description="Recipient email addresses")
    use_tls: bool = Field(default=True, description="Use TLS for SMTP")

    @field_validator("to_addresses")
    @classmethod
    def validate_to_addresses(cls, v: list[str]) -> list[str]:
        """Validate email addresses."""
        if not v:
            return v
        try:
            from email_validator import validate_email, EmailNotValidError
        except ImportError:
            # If email-validator is not available, skip validation
            return v

        validated = []
        for addr in v:
            try:
                validate_email(addr, check_deliverability=False)
                validated.append(addr)
            except EmailNotValidError:
                raise ValueError(f"Invalid email address: {addr}")
        return validated


class TeamsConfig(BaseModel):
    """Microsoft Teams webhook configuration."""

    enabled: bool = Field(default=False, description="Enable Teams notifications")
    webhook_url: str = Field(default="", description="Teams webhook URL")


class NotificationConfig(BaseModel):
    """Notification configuration."""

    email: EmailConfig = Field(default_factory=EmailConfig)
    teams: TeamsConfig = Field(default_factory=TeamsConfig)


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    log_file: Optional[str] = Field(
        default=None,
        description="Path to log file (None = output to terminal only, default). If specified, logs will be written to both terminal and file.",
    )
    log_format: Optional[str] = Field(
        default=None, description="Custom log format string (optional)"
    )
    date_format: Optional[str] = Field(
        default=None, description="Custom date format string (optional)"
    )


class MemoryConfig(BaseModel):
    """Memory configuration for long-term report history."""

    enabled: bool = Field(default=False, description="Enable mem0 memory storage")
    storage_path: str = Field(
        default="config/mem0",
        description="Local storage path for mem0 data (when using local mode)",
    )
    namespace: str = Field(default="grafana-agent", description="mem0 user_id/namespace")
    history_mode: str = Field(
        default="days",
        description="History retrieval mode: 'days' or 'count'",
    )
    history_days: int = Field(default=30, description="Days of history to include")
    history_count: int = Field(default=20, description="Number of reports to include")
    max_history_chars: int = Field(
        default=8000,
        description="Max characters of history passed into long-term summary prompt",
    )

    @field_validator("history_mode")
    @classmethod
    def validate_history_mode(cls, v: str) -> str:
        value = (v or "").strip().lower()
        if value not in {"days", "count"}:
            raise ValueError("history_mode must be 'days' or 'count'")
        return value


class AppConfig(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    grafana: GrafanaConfig
    llm: LLMConfig
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    timezone: str = Field(default="UTC", description="Timezone for time calculations")
    lookback_hours: int = Field(default=24, description="Hours to look back for inspection")
    language: str = Field(default="zh", description="Report language: 'zh' for Chinese, 'en' for English")

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "AppConfig":
        """Load configuration from YAML file, with env var overrides."""
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # Apply environment variable overrides on top of YAML values
        cls._apply_env_overrides(data)

        return cls(**data)

    @staticmethod
    def _apply_env_overrides(data: dict) -> None:
        """Override config dict values with non-empty environment variables."""
        for env_key, path, converter in _ENV_OVERRIDES:
            value = os.getenv(env_key)
            if not value:
                continue
            target = data
            for key in path[:-1]:
                if key not in target:
                    target[key] = {}
                target = target[key]
            target[path[-1]] = converter(value)

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load configuration from environment variables."""
        return cls(
            grafana=GrafanaConfig(
                url=os.getenv("GRAFANA_URL", ""),
                api_key=os.getenv("GRAFANA_API_KEY", ""),
                timeout=int(os.getenv("GRAFANA_TIMEOUT", "30")),
                verify_ssl=os.getenv("GRAFANA_VERIFY_SSL", "false").lower() == "true",
                ca_file=os.getenv("GRAFANA_CA_FILE") or None,
                slow_query_dashboard_uids=[
                    uid.strip()
                    for uid in os.getenv(
                        "GRAFANA_SLOW_QUERY_DASHBOARD_UIDS",
                        os.getenv("GRAFANA_SLOW_QUERY_DASHBOARD_UID", "aawp84s"),
                    ).split(",")
                    if uid.strip()
                ],
            ),
            llm=LLMConfig(
                provider=os.getenv("LLM_PROVIDER", "github_copilot"),
                access_token=os.getenv("COPILOT_ACCESS_TOKEN", os.getenv("LLM_API_KEY", "")),
                model=os.getenv("LLM_MODEL", "gpt5.2"),
                api_base=os.getenv("COPILOT_API_BASE", "https://api.githubcopilot.com"),
                token_url=os.getenv(
                    "COPILOT_TOKEN_URL",
                    "https://api.github.com/copilot_internal/v2/token",
                ),
                editor_version=os.getenv("COPILOT_EDITOR_VERSION", "vscode/1.99.0"),
                editor_plugin_version=os.getenv("COPILOT_EDITOR_PLUGIN_VERSION", "copilot-chat/0.26.7"),
                user_agent=os.getenv("COPILOT_USER_AGENT", "GitHubCopilotChat/0.26.7"),
                temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
                max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2000")),
                request_timeout=int(os.getenv("LLM_REQUEST_TIMEOUT", "180")),
                chunk_max_retries=int(os.getenv("LLM_CHUNK_MAX_RETRIES", "2")),
                chunk_retry_backoff_seconds=float(
                    os.getenv("LLM_CHUNK_RETRY_BACKOFF_SECONDS", "1.0")
                ),
                chunk_retry_max_backoff_seconds=float(
                    os.getenv("LLM_CHUNK_RETRY_MAX_BACKOFF_SECONDS", "8.0")
                ),
                jvm_max_panels=int(os.getenv("LLM_JVM_MAX_PANELS", "100")),
                jvm_keywords=[
                    kw.strip()
                    for kw in os.getenv("LLM_JVM_KEYWORDS", "").split(",")
                    if kw.strip()
                ]
                or [
                    "jvm", "heap", "non-heap", "nonheap", "gc", "garbage",
                    "eden", "survivor", "old gen", "tenured", "metaspace",
                    "codecache", "code cache", "thread", "class loading",
                    "jvm_memory", "jvm_gc", "jvm_threads", "jvm_buffer",
                    "process_cpu", "hikari", "tomcat", "java_lang",
                    "direct_buffer", "mapped_buffer", "g1", "young gen",
                ],
            ),
            notification=NotificationConfig(
                email=EmailConfig(
                    enabled=os.getenv("EMAIL_ENABLED", "false").lower() == "true",
                    smtp_host=os.getenv("SMTP_HOST", ""),
                    smtp_port=int(os.getenv("SMTP_PORT", "587")),
                    smtp_user=os.getenv("SMTP_USER", ""),
                    smtp_password=os.getenv("SMTP_PASSWORD", ""),
                    from_address=os.getenv("EMAIL_FROM", ""),
                    to_addresses=os.getenv("EMAIL_TO", "").split(",") if os.getenv("EMAIL_TO") else [],
                    use_tls=os.getenv("SMTP_USE_TLS", "true").lower() == "true",
                ),
                teams=TeamsConfig(
                    enabled=os.getenv("TEAMS_ENABLED", "false").lower() == "true",
                    webhook_url=os.getenv("TEAMS_WEBHOOK_URL", ""),
                ),
            ),
            logging=LoggingConfig(
                level=os.getenv("LOG_LEVEL", "INFO"),
                log_file=os.getenv("LOG_FILE"),
                log_format=os.getenv("LOG_FORMAT"),
                date_format=os.getenv("LOG_DATE_FORMAT"),
            ),
            memory=MemoryConfig(
                enabled=os.getenv("MEMORY_ENABLED", "false").lower() == "true",
                storage_path=os.getenv("MEMORY_STORAGE_PATH", "config/mem0"),
                namespace=os.getenv("MEMORY_NAMESPACE", "grafana-agent"),
                history_mode=os.getenv("MEMORY_HISTORY_MODE", "days"),
                history_days=int(os.getenv("MEMORY_HISTORY_DAYS", "30")),
                history_count=int(os.getenv("MEMORY_HISTORY_COUNT", "20")),
                max_history_chars=int(os.getenv("MEMORY_MAX_HISTORY_CHARS", "8000")),
            ),
            timezone=os.getenv("TIMEZONE", "UTC"),
            lookback_hours=int(os.getenv("LOOKBACK_HOURS", "24")),
            language=os.getenv("LANGUAGE", "zh"),
        )

