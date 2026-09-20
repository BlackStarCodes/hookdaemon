"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.health import router as health_router
from app.api.v1.router import api_router
from app.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.middleware import RequestIDMiddleware
from app.db import engine

setup_logging()
settings = get_settings()

logger = get_logger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Dispose the SQLAlchemy engine on shutdown.

    A failed dispose must not crash the process — the engine is garbage-
    collected regardless, and shutdown-time exceptions are a common source
    of non-zero exit codes in containerized services.
    """
    yield
    try:
        await engine.dispose()
    except Exception:
        logger.exception("engine_dispose_failed")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)
# Probes live at root: /health/live, /health/ready.
app.include_router(health_router)
# Business endpoints live under /v1/*.
app.include_router(api_router)


# RequestIDMiddleware is the only user middleware in v1. If more are added
# later, this must remain the LAST add_middleware() call so it runs first
# and wraps everything else.
app.add_middleware(RequestIDMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Catch-all for exceptions that FastAPI does not handle itself.

    Logs the traceback once, then returns a generic 500. The request_id is
    read from contextvars (bound by RequestIDMiddleware) and echoed in the
    response header so a client can correlate.
    """
    request_id = structlog.contextvars.get_contextvars().get("request_id", "")
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        exc_info=exc,
    )
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers=headers,
    )
