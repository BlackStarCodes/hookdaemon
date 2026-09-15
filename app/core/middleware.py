"""Pure ASGI middleware that assigns and propagates a request ID.

Pure ASGI (not ``BaseHTTPMiddleware``) because:

- ``BaseHTTPMiddleware`` runs each request in a separate task, which breaks
some contextvars propagation patterns and adds per-request anyio task-group
overhead (measurably ~15% of frames in deep stacks).
- It buffers streaming responses.
- It does not give us access to the raw ``http.response.start`` ASGI message,
which is where we inject the response header.

Responsibilities:

- Read a sanitized ``X-Request-ID`` from the incoming request if present.
- Otherwise generate a new UUID4 hex.
- Bind ``request_id`` into ``structlog.contextvars`` for every log line in the
request scope.
- Echo the id back to the client in the ``X-Request-ID`` response header.
- Emit one structured access log per request (skipped for infra endpoints).

Middleware ordering note: Starlette runs the **last-added** user middleware
outermost. If more middleware is added later, ``RequestIDMiddleware`` must be
added **last** so it runs first and wraps everything else.
"""

import re
import time
import uuid
from collections.abc import MutableMapping
from typing import Any

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger

_REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = b"x-request-id"
_MAX_REQUEST_ID_LEN = 64
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Polled by load balancers and Prometheus. Skip access logs for these so they
# do not drown the signal at ~10 lines/min/endpoint.
#
# Exact-match only (not prefix). New health or metrics endpoints must be
# added explicitly. FastAPI normalizes trailing slashes by default, so
# ``/health/live/`` resolves to ``/health/live`` before it reaches us.
_SKIP_ACCESS_LOG_PATHS: frozenset[str] = frozenset({"/health/live", "/health/ready", "/metrics"})

logger = get_logger("app.http")


def _sanitize_request_id(raw: bytes | None) -> str | None:
    """Return a safe request id or None.

    Rejects non-ASCII, over-long, or character-set-violating values so a
    client cannot inject log or HTTP headers via this field.
    """

    if not raw:
        return None
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if not _REQUEST_ID_PATTERN.fullmatch(value):
        return None
    if len(value) > _MAX_REQUEST_ID_LEN:
        return None
    return value


class RequestIDMiddleware:
    """Pure ASGI middleware. No ``BaseHTTPMiddleware``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only HTTP requests are of interest. Lifespan, websocket, etc. pass
        # through untouched.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._extract_request_id(scope) or uuid.uuid4().hex
        method = scope.get("method", "")
        path = scope.get("path", "")

        # Fresh context per request. Prevents leakage between concurrent
        # requests on the same worker under some ASGI servers.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_holder: MutableMapping[str, Any] = {"status_code": 500}
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status_code"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers[_REQUEST_ID_HEADER] = request_id
            await send(message)

        skip_log = path in _SKIP_ACCESS_LOG_PATHS

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            # The global exception handler in app.main logs the traceback.
            # We only log the access line here.
            if not skip_log:
                logger.error(
                    "http_request_error", method=method, path=path, duration_ms=duration_ms
                )
            raise
        else:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            if not skip_log:
                logger.info(
                    "http_request",
                    method=method,
                    path=path,
                    status_code=status_holder["status_code"],
                    duration_ms=duration_ms,
                )

    @staticmethod
    def _extract_request_id(scope: Scope) -> str | None:
        for name, value in scope.get("headers", []):
            if name == _REQUEST_ID_HEADER_BYTES:
                return _sanitize_request_id(value)
        return None
