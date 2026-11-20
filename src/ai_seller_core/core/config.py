from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    app_name: str = "AI Seller Core"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    database_url: SecretStr
    qdrant_url: AnyHttpUrl | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection_name: str = Field(default="ai_seller_vectors", min_length=1)
    secret_encryption_key: SecretStr | None = None
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    job_poll_interval_seconds: float = 1.0
    job_lease_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("log_format", mode="before")
    @classmethod
    def normalize_log_format(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        raw_value = value.get_secret_value().strip()
        if not raw_value:
            raise ValueError("DATABASE_URL must not be empty")
        if _is_placeholder(raw_value):
            raise ValueError("DATABASE_URL must not contain a placeholder")

        try:
            url = make_url(raw_value)
        except ArgumentError:
            raise ValueError("DATABASE_URL must be a valid PostgreSQL URL") from None
        if url.drivername != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        return value

    @field_validator("qdrant_api_key", "secret_encryption_key")
    @classmethod
    def validate_optional_secrets(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw_value = value.get_secret_value().strip()
        if not raw_value:
            raise ValueError("Configured secrets must not be empty")
        if _is_placeholder(raw_value):
            raise ValueError("Configured secrets must not contain placeholders")
        return value

    @model_validator(mode="after")
    def validate_deployment_profile(self) -> "Settings":
        if self.app_env in {"staging", "production"}:
            if self.qdrant_url is None:
                raise ValueError(f"QDRANT_URL is required for the {self.app_env} profile")
            if self.secret_encryption_key is None:
                raise ValueError(
                    f"SECRET_ENCRYPTION_KEY is required for the {self.app_env} profile"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _is_placeholder(value: str) -> bool:
    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in (
            "change-me",
            "change_me",
            "changeme",
            "your-password",
            "your_password",
            "placeholder",
        )
    )
