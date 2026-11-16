import pytest
from pydantic import SecretStr, ValidationError

from ai_seller_core.core.config import Settings

DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/ai_seller"


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, database_url=DATABASE_URL, **overrides)


def test_development_profile_allows_local_settings_without_external_secrets() -> None:
    settings = make_settings(app_env="development")

    assert settings.qdrant_url is None
    assert settings.secret_encryption_key is None


@pytest.mark.parametrize(
    ("overrides", "missing_setting"),
    [
        ({"app_env": "production", "secret_encryption_key": "test-key"}, "QDRANT_URL"),
        (
            {"app_env": "staging", "qdrant_url": "https://qdrant.example.com"},
            "SECRET_ENCRYPTION_KEY",
        ),
    ],
)
def test_production_profiles_require_qdrant_and_encryption_key(
    overrides: dict[str, object],
    missing_setting: str,
) -> None:
    with pytest.raises(ValidationError, match=missing_setting):
        make_settings(**overrides)


def test_production_profile_accepts_required_configuration() -> None:
    settings = make_settings(
        app_env="production",
        qdrant_url="https://qdrant.example.com",
        secret_encryption_key="secret-key",
    )

    assert isinstance(settings.database_url, SecretStr)
    assert isinstance(settings.secret_encryption_key, SecretStr)
    assert str(settings.database_url) == "**********"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "postgresql+asyncpg://user:CHANGE_ME@localhost:5432/ai_seller",
        "postgresql://user:password@localhost:5432/ai_seller",
        "not-a-url",
    ],
)
def test_database_url_rejects_empty_placeholder_or_unsupported_urls(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=database_url)


def test_invalid_profile_is_rejected() -> None:
    with pytest.raises(ValidationError, match="app_env"):
        make_settings(app_env="prod")
