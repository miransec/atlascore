"""
Tests for Settings startup validation.

These tests deliberately construct Settings() with bad inputs and assert
they raise ValueError at import/construction time.

Coverage:
  1.  REPLACE_* placeholder in any secret field is rejected
  2.  JWT_SECRET_KEY shorter than 64 bytes is rejected
  3.  Secrets shorter than 32 bytes are rejected
  4.  Wildcard ALLOWED_ORIGINS in production is rejected
  5.  SECURE_COOKIES=false in production is rejected
  6.  Valid config instantiates without error
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_BASE = {
    "DATABASE_URL": "postgresql+asyncpg://a:b@localhost:5432/c",
    "REDIS_URL": "redis://localhost:6379/0",
    "JWT_SECRET_KEY": "j" * 64,
    "REFRESH_TOKEN_PEPPER": "r" * 32,
    "ARGON2_PEPPER": "a" * 32,
    "ARGON2_PEPPER_VERSION": "1",
    "CSRF_SECRET": "c" * 32,
    "PRE_AUTH_SESSION_PEPPER": "p" * 32,
    "ALLOWED_ORIGINS": "http://localhost:3100",
    "ENVIRONMENT": "development",
    "SECURE_COOKIES": "false",
}


def _make(**overrides) -> dict:
    return {**_BASE, **overrides}


def test_valid_config_instantiates() -> None:
    s = Settings(**_make())
    assert s.ENVIRONMENT == "development"


def test_replace_placeholder_rejected() -> None:
    with pytest.raises(ValidationError, match="REPLACE_"):
        Settings(
            **_make(
                JWT_SECRET_KEY="REPLACE_ME_REPLACE_ME_REPLACE_ME_REPLACE_ME_REPLACE_ME_REPLACE_ME_64"
            )
        )


def test_jwt_secret_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**_make(JWT_SECRET_KEY="short"))


def test_argon2_pepper_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**_make(ARGON2_PEPPER="tooshort"))


def test_csrf_secret_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**_make(CSRF_SECRET="short"))


def test_pre_auth_pepper_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**_make(PRE_AUTH_SESSION_PEPPER="short"))


def test_refresh_pepper_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**_make(REFRESH_TOKEN_PEPPER="short"))


def test_wildcard_origin_in_production_rejected() -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(
            **_make(
                ENVIRONMENT="production",
                ALLOWED_ORIGINS="*",
                SECURE_COOKIES="true",
            )
        )


def test_insecure_cookies_in_production_rejected() -> None:
    with pytest.raises(ValidationError, match="SECURE_COOKIES"):
        Settings(
            **_make(
                ENVIRONMENT="production",
                ALLOWED_ORIGINS="https://app.example.com",
                SECURE_COOKIES="false",
            )
        )


def test_production_valid_config_instantiates() -> None:
    s = Settings(
        **_make(
            ENVIRONMENT="production",
            ALLOWED_ORIGINS="https://app.example.com",
            SECURE_COOKIES="true",
        )
    )
    assert s.is_production is True


def test_allowed_origins_list_parsed() -> None:
    s = Settings(**_make(ALLOWED_ORIGINS="https://a.example.com,https://b.example.com"))
    origins = s.allowed_origins_list
    assert "https://a.example.com" in origins
    assert "https://b.example.com" in origins
    assert len(origins) == 2
