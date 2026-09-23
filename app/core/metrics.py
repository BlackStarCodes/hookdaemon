"""Prometheus metrics exposition endpoint.

Uses the prometheus-client default registry. Importing
``prometheus_client`` auto-registers Python process and GC collectors, so
``/metrics`` is non-empty without explicit instrumentation. Domain metrics
are added in later phases by registering counters, gauges, and histograms
on the same default registry.

Single-process assumption: the compose stack runs one uvicorn process. If
the API scales to multiple workers, each process would expose only its own
metrics. Multiprocess aggregation requires PROMETHEUS_MULTIPROC_DIR and a
MultiProcessCollector on a custom registry.
"""

import asyncio

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Expose Prometheus metrics in text exposition format.

    ``generate_latest`` walks the registry and serializes it to text, which
    is CPU-bound. Offloaded to a thread so a scrape under load cannot stall
    the event loop.
    """

    content = await asyncio.to_thread(generate_latest)
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)
