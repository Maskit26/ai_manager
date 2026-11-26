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


def test_jwt_settings_match_the_mvp_contract() -> None:
    settings = make_settings()

    assert settings.jwt_access_token_ttl_minutes == 15
    assert settings.jwt_issuer == "ai-manager-web"
    assert settings.jwt_audience == "ai-manager-api"
    assert settings.jwt_public_keys == {}
    assert settings.jwt_standalone_enabled is False


@pytest.mark.parametrize("ttl_minutes", [0, 16])
def test_jwt_access_token_ttl_must_be_between_one_and_fifteen_minutes(
    ttl_minutes: int,
) -> None:
    with pytest.raises(ValidationError):
        make_settings(jwt_access_token_ttl_minutes=ttl_minutes)


def test_jwt_public_keys_load_from_json_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_keys_json = (
        '{"issuer-2026":"-----BEGIN PUBLIC KEY-----\\nkey-data\\n-----END PUBLIC KEY-----"}'
    )
    monkeypatch.setenv("JWT_PUBLIC_KEYS", public_keys_json)

    settings = Settings(_env_file=None, database_url=DATABASE_URL)

    assert settings.jwt_public_keys == {
        "issuer-2026": "-----BEGIN PUBLIC KEY-----\nkey-data\n-----END PUBLIC KEY-----"
    }


@pytest.mark.parametrize(
    "jwt_public_keys",
    [
        {"": "public-key"},
        {"issuer-2026": ""},
        {"issuer-2026": "-----BEGIN PRIVATE KEY-----\nprivate-data"},
    ],
)
def test_jwt_public_keys_reject_empty_ids_values_and_private_keys(
    jwt_public_keys: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(jwt_public_keys=jwt_public_keys)


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_deployment_profiles_require_jwt_public_keys(app_env: str) -> None:
    with pytest.raises(ValidationError, match="JWT_PUBLIC_KEYS"):
        make_settings(
            app_env=app_env,
            qdrant_url="https://qdrant.example.com",
            secret_encryption_key="secret-key",
        )


def test_standalone_mode_requires_a_secret_and_is_limited_to_local_profiles() -> None:
    with pytest.raises(ValidationError, match="JWT_STANDALONE_SECRET"):
        make_settings(app_env="development", jwt_standalone_enabled=True)

    for app_env in ("development", "local"):
        settings = make_settings(
            app_env=app_env,
            jwt_standalone_enabled=True,
            jwt_standalone_secret="standalone-secret",
        )
        assert settings.jwt_standalone_enabled is True
        assert isinstance(settings.jwt_standalone_secret, SecretStr)

    for app_env in ("test", "staging", "production"):
        with pytest.raises(ValidationError, match="only allowed in development/local"):
            make_settings(
                app_env=app_env,
                jwt_standalone_enabled=True,
                jwt_standalone_secret="standalone-secret",
            )


def test_standalone_secret_cannot_be_configured_when_mode_is_disabled() -> None:
    with pytest.raises(ValidationError, match="JWT_STANDALONE_ENABLED"):
        make_settings(jwt_standalone_secret="standalone-secret")


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
        jwt_public_keys={
            "issuer-2026": ("-----BEGIN PUBLIC KEY-----\nkey-data\n-----END PUBLIC KEY-----")
        },
    )

    assert isinstance(settings.database_url, SecretStr)
    assert isinstance(settings.secret_encryption_key, SecretStr)
    assert str(settings.database_url) == "**********"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

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
