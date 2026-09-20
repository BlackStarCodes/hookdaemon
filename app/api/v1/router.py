"""Aggregate all v1 routers.

Health endpoints are not under /v1 — they are registered directly on the
app so probes live at /health/live and /health/ready."""

from fastapi import APIRouter

api_router = APIRouter(prefix="/v1")
