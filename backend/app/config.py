"""Application settings loaded from environment variables.

Single source of truth for all env-driven configuration. No other
module should read ``os.environ`` directly.
"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root holds the canonical .env (one level above backend/).
# Resolved from this file so the path is invariant across cwds.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """All env-driven configuration.

    Fields without a default value are required; missing them at
    construction time raises ``pydantic.ValidationError``.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    DATABASE_URL: str
    """Async Postgres URL; must use the +asyncpg driver."""

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"
    """DB 0 = production data; DB 15 reserved for integration tests."""

    # --- LLM APIs ---
    DASHSCOPE_API_KEY: str
    DEEPSEEK_API_KEY: str = ""

    # --- Feishu ---
    FEISHU_APP_ID: str
    FEISHU_APP_SECRET: str
    FEISHU_VERIFICATION_TOKEN: str = ""
    FEISHU_ADMIN_OPEN_ID: str = ""
    FEISHU_ADMIN_CHAT_ID: str = ""

    # --- Langfuse ---
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "http://localhost:3100"

    # --- Application ---
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    APP_SECRET_KEY: str
    """JWT signing key; production value should be at least 32 chars."""

    # --- Workspace auth (M7-B) ---
    APP_WORKSPACE_USERNAME: str = "admin"
    APP_WORKSPACE_PASSWORD: str

    # --- Encryption ---
    COOKIE_ENCRYPTION_KEY: str
    """Fernet key (urlsafe-base64 32 bytes) for sellers.cookie_encrypted."""


settings = Settings()  # type: ignore[call-arg]
"""Module-level singleton; fields populated from env / .env at import time."""
