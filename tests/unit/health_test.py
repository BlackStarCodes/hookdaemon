"""Tests for the health endpoints."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from app.api.v1.health import router as health_router
from app.db import get_session


class _StubResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _StubSession:
    """Minimal AsyncSession stub: returns canned results or raises."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = responses

    async def execute(self, *_args: Any, **_kwargs: Any) -> _StubResult:
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return _StubResult(result)


def _app_with(session: _StubSession) -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)

    async def override() -> AsyncIterator[_StubSession]:
        yield session

    app.dependency_overrides[get_session] = override
    return app


def test_liveness_returns_ok() -> None:
    app = FastAPI()
    app.include_router(health_router)
    with TestClient(app) as client:
        r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readiness_ok_when_db_and_migration_present() -> None:
    session = _StubSession([None, "abc123"])
    with TestClient(_app_with(session)) as client:
        r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["postgres"] == {"status": "ok", "migration": "abc123"}
    assert body["redis"]["status"] == "not_configured"


def test_readiness_returns_503_when_migrations_not_applied() -> None:
    session = _StubSession(
        [None, ProgrammingError("no table", None, RuntimeError("undefined_table"))]
    )
    with TestClient(_app_with(session)) as client:
        r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["postgres"]["status"] == "unreachable"
    assert body["postgres"]["error"] == "migrations_not_applied"


def test_readiness_returns_503_when_db_unreachable() -> None:
    session = _StubSession([RuntimeError("simulated: database unreachable")])
    with TestClient(_app_with(session)) as client:
        r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["postgres"]["status"] == "unreachable"


def test_readiness_returns_db_error_when_second_query_fails() -> None:
    """If the DB fails mid-check (after SELECT 1 succeeded), report the error."""
    session = _StubSession([None, RuntimeError("connection lost")])
    with TestClient(_app_with(session)) as client:
        r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["postgres"]["status"] == "unreachable"
    assert body["postgres"]["error"] == "RuntimeError"


def test_readiness_returns_503_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB query that exceeds the health-check timeout reports 'timeout'."""
    monkeypatch.setattr("app.api.v1.health._HEALTH_DB_TIMEOUT_SECONDS", 0.05)

    class _HangingSession:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            await asyncio.sleep(10)
            raise AssertionError("should have been cancelled")

    app = FastAPI()
    app.include_router(health_router)

    async def override() -> AsyncIterator[_HangingSession]:
        yield _HangingSession()

    app.dependency_overrides[get_session] = override

    with TestClient(app) as client:
        r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["postgres"]["error"] == "timeout"


def test_readiness_returns_503_when_alembic_version_empty() -> None:
    """Query succeeds but the table is empty — migrations partially applied."""
    session = _StubSession([None, None])  # SELECT 1 ok, version is None
    with TestClient(_app_with(session)) as client:
        r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["postgres"]["status"] == "unreachable"
    assert body["postgres"]["error"] == "alembic_version_empty"
