"""First-party Prometheus metrics middleware and /metrics endpoint.

Replaces ``prometheus-fastapi-instrumentator``, which breaks against
FastAPI >= 0.139 (router entries without ``.path`` crash its route-name
lookup). Owning these ~40 lines removes the compatibility surface entirely:
a request counter and a latency histogram, labeled by method, route template
(never the raw path — label cardinality stays bounded), and status code,
exposed from prometheus-client's default registry (which also provides the
standard python/process collectors).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS_TOTAL = Counter(
    "ic_http_requests_total",
    "HTTP requests processed, by method, route template, and status code.",
    labelnames=("method", "path", "status"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "ic_http_request_duration_seconds",
    "HTTP request latency in seconds, by method and route template.",
    labelnames=("method", "path"),
)


def _route_template(request: Request) -> str:
    """The matched route's template (e.g. ``/api/v1/users/me``), never the raw path."""
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
    return str(path_format) if path_format else "unmatched"


def register_metrics(app: FastAPI, *, endpoint: str = "/metrics") -> None:
    """Attach the metrics middleware and mount the scrape endpoint."""

    @app.middleware("http")
    async def observe_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == endpoint:
            return await call_next(request)

        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started

        template = _route_template(request)
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method, path=template, status=str(response.status_code)
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, path=template).observe(elapsed)
        return response

    @app.get(endpoint, include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
