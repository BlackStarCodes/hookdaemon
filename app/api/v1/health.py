"""Liveness and readiness endpoints.

- ``/health/live`` — process is running; no dependency checks.
- ``/health/ready`` — service can accept traffic: Postgres reachable,
  ``alembic_version`` present. Redis is reported as not_configured;
  it does not gate readiness.

Readiness returns 503 when the service is not ready so load balancers can
remove the instance without parsing the body.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

_HEALTH_DB_TIMEOUT_SECONDS = 2

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


async def _postgres_status(session: AsyncSession) -> dict[str, str]:
    """Return a health status dict for Postgres.

    Keys:
    - ``status``: ``"ok"`` or ``"unreachable"``.
    - ``migration``: version string, present only when ``status == "ok"``.
    - ``error``: short code (``timeout``, ``migrations_not_applied``,
      ``alembic_version_empty``) or exception class name, present only
      when ``status == "unreachable"``.
    """

    try:
        await asyncio.wait_for(
            session.execute(text("SELECT 1")),
            timeout=_HEALTH_DB_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return {"status": "unreachable", "error": "timeout"}
    except Exception as exc:  # noqa: BLE001 — health check must catch everything
        return {"status": "unreachable", "error": type(exc).__name__}

    try:
        result = await asyncio.wait_for(
            session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")),
            timeout=_HEALTH_DB_TIMEOUT_SECONDS,
        )
        version = result.scalar()
    except TimeoutError:
        return {"status": "unreachable", "error": "timeout"}
    except ProgrammingError:
        # Table does not exist yet — migrations have not been applied.
        return {"status": "unreachable", "error": "migrations_not_applied"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unreachable", "error": type(exc).__name__}

    if not version:
        return {"status": "unreachable", "error": "alembic_version_empty"}

    return {"status": "ok", "migration": version}


@router.get("/ready")
async def readiness(session: Annotated[AsyncSession, Depends(get_session)]) -> JSONResponse:
    pg = await _postgres_status(session)
    ready = pg["status"] == "ok"
    body = {
        "postgres": pg,
        "redis": {"status": "not_configured"},
    }
    code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=body)
