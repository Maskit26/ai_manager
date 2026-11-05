from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Seller Core"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://ai_seller:change-me@127.0.0.1:5432/ai_seller"
    job_poll_interval_seconds: float = 1.0
    job_lease_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
