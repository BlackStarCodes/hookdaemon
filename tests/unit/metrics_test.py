"""Tests for the Prometheus /metrics endpoint."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.metrics import router as metrics_router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(metrics_router)
    return app


def test_metrics_endpoint_returns_200() -> None:
    with TestClient(_app()) as client:
        r = client.get("/metrics")
    assert r.status_code == 200


def test_metrics_content_type_is_prometheus_text() -> None:
    with TestClient(_app()) as client:
        r = client.get("/metrics")
    assert r.headers["content-type"].startswith("text/plain")


def test_metrics_body_uses_prometheus_exposition_format() -> None:
    """Prometheus text format requires # HELP and # TYPE markers."""
    with TestClient(_app()) as client:
        r = client.get("/metrics")
    assert "# HELP" in r.text
    assert "# TYPE" in r.text


def test_metrics_hidden_from_openapi_schema() -> None:
    """include_in_schema=False hides /metrics while other routes appear."""
    app = FastAPI()

    @app.get("/visible")
    async def visible() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(metrics_router)

    with TestClient(app) as client:
        r = client.get("/openapi.json")

    paths = r.json()["paths"]
    assert "/visible" in paths
    assert "/metrics" not in paths
