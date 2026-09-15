"""FastAPI application factory."""

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.middleware import RequestIDMiddleware

setup_logging()
settings = get_settings()

logger = get_logger("app")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

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
    responder header so a client can correlate.
    """
    request_id = structlog.contextvars.get_contextvars().get("request_id", "")
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
    )
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers=headers,
    )


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}
