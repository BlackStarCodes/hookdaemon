"""Tests for app.core.middleware.RequestIDMiddleware."""

from collections.abc import Iterator
from io import StringIO

import pytest
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.logging import setup_logging
from app.core.middleware import (
    _MAX_REQUEST_ID_LEN,
    _REQUEST_ID_HEADER,
    RequestIDMiddleware,
)
from tests.helpers import parse_json_lines


def _build_app() -> FastAPI:
    """Minimal FastAPI app with the middleware and a global exception handler."""
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = structlog.contextvars.get_contextvars().get("request_id", "")
        headers = {_REQUEST_ID_HEADER: request_id} if request_id else None
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
            headers=headers,
        )

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("intentional failure")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def buf() -> Iterator[StringIO]:
    b = StringIO()
    setup_logging(Settings(_env_file=None), stream=b)  # type: ignore[call-arg]
    structlog.contextvars.clear_contextvars()
    yield b
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def client(buf: StringIO) -> Iterator[TestClient]:
    with TestClient(_build_app(), raise_server_exceptions=False) as c:
        yield c


def test_generates_request_id_when_absent(client: TestClient) -> None:
    r = client.get("/ping")
    assert r.status_code == 200
    assert _REQUEST_ID_HEADER in r.headers
    assert len(r.headers[_REQUEST_ID_HEADER]) == 32


def test_uses_incoming_request_id_when_valid(client: TestClient) -> None:
    r = client.get("/ping", headers={_REQUEST_ID_HEADER: "client-123"})
    assert r.status_code == 200
    assert r.headers[_REQUEST_ID_HEADER] == "client-123"


def test_rejects_unsafe_request_id(client: TestClient) -> None:
    # Contains whitespace, not allowed by the pattern.
    r = client.get("/ping", headers={_REQUEST_ID_HEADER: "abc def"})
    assert r.status_code == 200
    generated = r.headers[_REQUEST_ID_HEADER]
    assert generated != "abc def"
    assert len(generated) == 32


def test_rejects_overlong_request_id(client: TestClient) -> None:
    r = client.get("/ping", headers={_REQUEST_ID_HEADER: "a" * (_MAX_REQUEST_ID_LEN + 1)})
    assert r.status_code == 200
    assert len(r.headers[_REQUEST_ID_HEADER]) == 32  # regenerated


def test_access_log_contains_request_id(client: TestClient, buf: StringIO) -> None:
    client.get("/ping", headers={_REQUEST_ID_HEADER: "trace-me"})
    lines = parse_json_lines(buf)
    access = [line for line in lines if line.get("event") == "http_request"]
    assert len(access) == 1
    entry = access[0]
    assert entry["request_id"] == "trace-me"
    assert entry["method"] == "GET"
    assert entry["path"] == "/ping"
    assert entry["status_code"] == 200
    assert "duration_ms" in entry


def test_error_path_sets_response_header(client: TestClient, buf: StringIO) -> None:
    r = client.get("/boom", headers={_REQUEST_ID_HEADER: "trace-error"})
    assert r.status_code == 500
    assert r.headers[_REQUEST_ID_HEADER] == "trace-error"

    lines = parse_json_lines(buf)
    error_logs = [line for line in lines if line.get("event") == "http_request_error"]
    assert len(error_logs) == 1
    assert error_logs[0]["request_id"] == "trace-error"
    assert error_logs[0]["path"] == "/boom"


def test_request_ids_do_not_leak_between_requests(client: TestClient, buf: StringIO) -> None:
    r1 = client.get("/ping")
    r2 = client.get("/ping")
    assert r1.headers[_REQUEST_ID_HEADER] != r2.headers[_REQUEST_ID_HEADER]


def test_health_live_access_log_is_skipped(client: TestClient, buf: StringIO) -> None:
    client.get("/health/live")
    lines = parse_json_lines(buf)
    access = [line for line in lines if line.get("event") == "http_request"]
    assert access == []


def test_rejects_newline_injection(client: TestClient) -> None:
    """CWE-117: newlines in the header must not reach the log.

    A client-supplied CR/LF could forge log lines (log injection) or split
    the response header (HTTP response splitting). The sanitizer rejects the
    value; a fresh id is generated.
    """
    r = client.get("/ping", headers={_REQUEST_ID_HEADER: "abc\ninjected"})
    assert r.status_code == 200
    assert "\n" not in r.headers[_REQUEST_ID_HEADER]
    assert "\r" not in r.headers[_REQUEST_ID_HEADER]
    assert len(r.headers[_REQUEST_ID_HEADER]) == 32
