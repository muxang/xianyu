"""Tests for app.config Settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, settings

# Minimum required fields to construct a valid Settings without .env.
# Mirrors the no-default fields declared on the Settings class.
REQUIRED_KWARGS: dict[str, str] = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "DASHSCOPE_API_KEY": "sk-test",
    "FEISHU_APP_ID": "cli_test",
    "FEISHU_APP_SECRET": "test_secret",
    "APP_SECRET_KEY": "test_jwt_secret_at_least_32_chars_long",
    "APP_WORKSPACE_PASSWORD": "test_password",
    "COOKIE_ENCRYPTION_KEY": "test_fernet_key",
}


def test_module_singleton_loaded_from_env() -> None:
    """The .env-driven singleton constructed at import time has all fields."""
    assert settings.DATABASE_URL.startswith("postgresql+asyncpg://")
    assert settings.APP_ENV in ("development", "staging", "production")
    assert settings.APP_LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR")
    assert settings.REDIS_URL.startswith("redis://")


def test_explicit_construction_with_all_required_fields() -> None:
    """Can construct Settings without .env when all required kwargs are given."""
    s = Settings(_env_file=None, **REQUIRED_KWARGS)  # type: ignore[call-arg, arg-type]
    # Spot-check that constructor kwargs flowed through and defaults applied.
    assert s.DASHSCOPE_API_KEY == "sk-test"
    assert s.APP_ENV == "development"  # default
    assert s.APP_LOG_LEVEL == "INFO"  # default
    assert s.REDIS_URL == "redis://localhost:6379/0"  # default
    assert s.DEEPSEEK_API_KEY == ""  # default


def test_missing_required_field_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop one required field from both env and kwargs; expect ValidationError."""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    partial = {k: v for k, v in REQUIRED_KWARGS.items() if k != "DASHSCOPE_API_KEY"}
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **partial)  # type: ignore[call-arg, arg-type]
    assert "DASHSCOPE_API_KEY" in str(exc.value)


def test_invalid_app_env_raises() -> None:
    """Literal-typed APP_ENV rejects values outside the allowed set."""
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, APP_ENV="weird_env", **REQUIRED_KWARGS)  # type: ignore[call-arg, arg-type]
    assert "APP_ENV" in str(exc.value)


def test_invalid_log_level_raises() -> None:
    """Literal-typed APP_LOG_LEVEL rejects values outside the allowed set."""
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, APP_LOG_LEVEL="VERBOSE", **REQUIRED_KWARGS)  # type: ignore[call-arg, arg-type]
    assert "APP_LOG_LEVEL" in str(exc.value)
