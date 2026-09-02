"""Environment-backed production runtime settings with fail-fast validation."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load all runtime configuration at the process boundary.

    Agent runtime code receives a configured provider and never reads credentials
    directly. The OpenAI key is required only when the OpenAI provider is selected.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    environment: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="DAYU_AGENT_ENV"
    )
    host: str = Field(default="0.0.0.0", validation_alias="DAYU_AGENT_HOST")
    port: int = Field(default=8000, ge=1, le=65535, validation_alias="DAYU_AGENT_PORT")
    model_provider: Literal["fake", "openai"] = Field(
        default="fake", validation_alias="MODEL_PROVIDER"
    )
    model_name: str = Field(default="gpt-5.6", min_length=1, validation_alias="MODEL_NAME")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    session_store: Literal["memory", "postgres"] = Field(
        default="memory", validation_alias="SESSION_STORE"
    )
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    database_pool_size: int = Field(default=5, ge=1, le=100, validation_alias="DB_POOL_SIZE")
    database_max_overflow: int = Field(
        default=10, ge=0, le=100, validation_alias="DB_MAX_OVERFLOW"
    )
    database_pool_timeout: float = Field(
        default=10.0, gt=0, le=120, validation_alias="DB_POOL_TIMEOUT_SECONDS"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", validation_alias="LOG_LEVEL"
    )
    provider_timeout_seconds: float = Field(
        default=60.0, gt=0, le=600, validation_alias="PROVIDER_TIMEOUT_SECONDS"
    )
    sdk_tracing_enabled: bool = Field(default=False, validation_alias="SDK_TRACING_ENABLED")
    retry_max_attempts: int = Field(
        default=3, ge=1, le=10, validation_alias="RETRY_MAX_ATTEMPTS"
    )
    retry_max_elapsed_seconds: float = Field(
        default=30.0, gt=0, le=600, validation_alias="RETRY_MAX_ELAPSED_SECONDS"
    )
    retry_base_delay_seconds: float = Field(
        default=0.25, ge=0, le=60, validation_alias="RETRY_BASE_DELAY_SECONDS"
    )
    retry_max_delay_seconds: float = Field(
        default=2.0, ge=0, le=120, validation_alias="RETRY_MAX_DELAY_SECONDS"
    )
    retry_jitter: bool = Field(default=True, validation_alias="RETRY_JITTER")
    stream_event_retention_seconds: int = Field(
        default=86400, ge=60, le=2592000, validation_alias="STREAM_EVENT_RETENTION_SECONDS"
    )
    max_request_bytes: int = Field(
        default=1_048_576, ge=1024, le=10_485_760, validation_alias="MAX_REQUEST_BYTES"
    )
    otel_enabled: bool = Field(default=False, validation_alias="OTEL_ENABLED")
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )

    @model_validator(mode="after")
    def validate_provider_credentials(self) -> "Settings":
        """Fail fast when an explicitly selected provider lacks credentials."""

        if self.model_provider == "openai" and (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai")
        if self.environment == "production" and self.session_store != "postgres":
            raise ValueError("SESSION_STORE=postgres is required in production")
        if self.session_store == "postgres":
            if not self.database_url:
                raise ValueError("DATABASE_URL is required when SESSION_STORE=postgres")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("DATABASE_URL must use postgresql+psycopg")
        if self.retry_max_delay_seconds < self.retry_base_delay_seconds:
            raise ValueError(
                "RETRY_MAX_DELAY_SECONDS must be greater than or equal to "
                "RETRY_BASE_DELAY_SECONDS"
            )
        return self

    def safe_summary(self) -> dict[str, str | int | float | bool]:
        """Return diagnostics that are safe to log or expose through readiness checks."""

        return {
            "environment": self.environment,
            "host": self.host,
            "port": self.port,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "session_store": self.session_store,
            "log_level": self.log_level,
            "provider_timeout_seconds": self.provider_timeout_seconds,
            "sdk_tracing_enabled": self.sdk_tracing_enabled,
            "retry_max_attempts": self.retry_max_attempts,
            "retry_max_elapsed_seconds": self.retry_max_elapsed_seconds,
            "stream_event_retention_seconds": self.stream_event_retention_seconds,
            "otel_enabled": self.otel_enabled,
            "otel_exporter_configured": bool(self.otel_exporter_otlp_endpoint),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create and cache the process-wide settings object."""

    return Settings()
