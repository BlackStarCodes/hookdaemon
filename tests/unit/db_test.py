"""Tests for app.db

These tests do not require a running Postgres. The engine is lazy: it does
not connect until a session is used, so construction is safe to test in
isolation.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db import SessionFactory, engine


def test_engine_is_async_engine() -> None:
    assert isinstance(engine, AsyncEngine)


def test_engine_uses_asyncpg_driver() -> None:
    """The engine must use asyncpg, not a sync fallback."""
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.dialect.driver == "asyncpg"
    assert engine.dialect.is_async is True


def test_session_factory_is_async_sessionmaker() -> None:
    assert isinstance(SessionFactory, async_sessionmaker)


def test_session_factory_disables_expire_on_commit() -> None:
    assert SessionFactory.kw["expire_on_commit"] is False


def test_session_factory_disables_autoflush() -> None:
    assert SessionFactory.kw["autoflush"] is False
