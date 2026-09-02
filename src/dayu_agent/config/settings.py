"""Environment-backed application settings with provider validation."""

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
    database_url: str = Field(
        default="postgresql+psycopg://dayu_agent:dayu_agent@localhost:5432/dayu_agent",
        validation_alias="DATABASE_URL",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", validation_alias="LOG_LEVEL"
    )
    provider_timeout_seconds: float = Field(
        default=60.0, gt=0, le=600, validation_alias="PROVIDER_TIMEOUT_SECONDS"
    )
    sdk_tracing_enabled: bool = Field(default=False, validation_alias="SDK_TRACING_ENABLED")

    @model_validator(mode="after")
    def validate_provider_credentials(self) -> "Settings":
        """Fail fast when an explicitly selected provider lacks credentials."""

        if self.model_provider == "openai" and (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai")
        return self

    def safe_summary(self) -> dict[str, str | int | float | bool]:
        """Return diagnostics that are safe to log or expose through readiness checks."""

        return {
            "environment": self.environment,
            "host": self.host,
            "port": self.port,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "log_level": self.log_level,
            "provider_timeout_seconds": self.provider_timeout_seconds,
            "sdk_tracing_enabled": self.sdk_tracing_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create and cache the process-wide settings object."""

    return Settings()
