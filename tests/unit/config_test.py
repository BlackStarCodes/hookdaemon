"""Tests for app.config.Settings and get_settings."""

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_defaults_are_sane() -> None:
    """Without env vars or .env, defaults must be usable for local dev."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.app_name == "hookdaemon"
    assert s.environment == "dev"
    assert s.log_level == "INFO"
    assert s.db_pool_size >= 1
    assert s.db_max_overflow >= 0
    assert s.db_pool_pre_ping is True


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables override defaults."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DB_POOL_SIZE", "20")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.environment == "test"
    assert s.log_level == "DEBUG"
    assert s.db_pool_size == 20


def test_invalid_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid Literal values are rejected at construction."""
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_pool_size_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """db_pool_size must be within the declared bounds."""
    monkeypatch.setenv("DB_POOL_SIZE", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]

    monkeypatch.setenv("DB_POOL_SIZE", "1000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_is_singleton() -> None:
    """get_settings returns the same instance across calls."""
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()
    assert b is a
    get_settings.cache_clear()
