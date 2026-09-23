"""Application configuration loaded from environment variables and .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Values come from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core ---
    app_name: str = "hookdaemon"
    app_version: str = "0.1.0"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Logging ---
    log_json: bool = True

    # --- Database (asyncpg driver) ---
    # Defaults target docker-compose's local Postgres. Override in .env
    # for anything non-local.
    database_url: str = Field(
        default="postgresql+asyncpg://hookdaemon:hookdaemon@localhost:5432/hookdaemon",  # pragma: allowlist secret
        description="PostgreSQL connection string using the asyncpg driver.",
    )
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_pre_ping: bool = True
    db_pool_recycle: int = Field(
        default=1800,
        ge=0,
        description="Seconds after which a pooled connection is recycled. "
        "0 disables. Prevents server-side idle timeouts from invalidating "
        "in-use connections.",
    )

    # --- API ---
    api_statement_timeout_ms: int = 10_000

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
