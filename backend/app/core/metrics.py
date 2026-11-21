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

import contextlib
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

# --------------------------------------------------------------------------- #
# Domain metrics (Phase 9). Labels are deliberately LOW-cardinality — the
# high-cardinality correlation IDs (run-id, case-id, request-id) live in the
# structured logs and the DB ledger tables, never as Prometheus labels. These
# primitives are pure (prometheus-client only) so the kernel layer stays
# import-clean; the call sites in the higher layers import these helpers.
# --------------------------------------------------------------------------- #

# --- Workflow / agent-node ---
WORKFLOW_RUNS_TOTAL = Counter(
    "ic_workflow_runs_total",
    "Workflow runs settled, by workflow key and terminal status.",
    labelnames=("workflow", "status"),
)
WORKFLOW_DURATION_SECONDS = Histogram(
    "ic_workflow_duration_seconds",
    "Workflow wall-clock duration in seconds, by workflow key.",
    labelnames=("workflow",),
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
AGENT_NODE_DURATION_SECONDS = Histogram(
    "ic_agent_node_duration_seconds",
    "Agent node wall-clock duration in seconds, by agent + status.",
    labelnames=("agent", "status"),
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30),
)

# --- LLM ---
LLM_CALLS_TOTAL = Counter(
    "ic_llm_calls_total",
    "LLM calls, by model, status, and execution mode (live/replay/cache/fallback).",
    labelnames=("model", "status", "mode"),
)
LLM_DURATION_SECONDS = Histogram(
    "ic_llm_duration_seconds",
    "LLM call latency in seconds, by model.",
    labelnames=("model",),
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 60),
)
LLM_TOKENS_TOTAL = Counter(
    "ic_llm_tokens_total",
    "LLM tokens, by model and direction (in/out).",
    labelnames=("model", "direction"),
)
LLM_COST_USD_TOTAL = Counter(
    "ic_llm_cost_usd_total",
    "Cumulative estimated LLM cost in USD, by model.",
    labelnames=("model",),
)

# --- Ingestion ---
INGEST_STAGE_DURATION_SECONDS = Histogram(
    "ic_ingest_stage_duration_seconds",
    "Ingestion stage duration in seconds, by stage + status.",
    labelnames=("stage", "status"),
    buckets=(0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60),
)
DOCUMENT_PROCESSING_FAILURES_TOTAL = Counter(
    "ic_document_processing_failures_total",
    "Document ingestion-stage failures, by stage.",
    labelnames=("stage",),
)

# --- ML + CAM ---
ML_INFERENCE_DURATION_SECONDS = Histogram(
    "ic_ml_inference_duration_seconds",
    "PD model inference duration in seconds.",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1),
)
ML_INFERENCE_TOTAL = Counter(
    "ic_ml_inference_total",
    "PD inferences, by availability outcome.",
    labelnames=("available",),
)
CAM_GENERATION_DURATION_SECONDS = Histogram(
    "ic_cam_generation_duration_seconds",
    "CAM assemble+render+persist duration in seconds, by status.",
    labelnames=("status",),
)

# --- Celery ---
CELERY_TASK_DURATION_SECONDS = Histogram(
    "ic_celery_task_duration_seconds",
    "Celery task execution duration in seconds, by task + status.",
    labelnames=("task", "status"),
    buckets=(0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
CELERY_QUEUE_LATENCY_SECONDS = Histogram(
    "ic_celery_queue_latency_seconds",
    "Celery enqueue→start latency in seconds, by task.",
    labelnames=("task",),
    buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10),
)
CELERY_TASKS_TOTAL = Counter(
    "ic_celery_tasks_total",
    "Celery tasks settled, by task and status.",
    labelnames=("task", "status"),
)

# --- Database ---
DB_QUERY_DURATION_SECONDS = Histogram(
    "ic_db_query_duration_seconds",
    "Database statement duration in seconds, by operation.",
    labelnames=("operation",),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2),
)


def _safe(fn: Callable[[], None]) -> None:
    """Metrics must never break a request/run — swallow any emit error."""
    with contextlib.suppress(Exception):  # observability is best-effort
        fn()


def observe_workflow(workflow: str, status: str, duration_s: float | None) -> None:
    _safe(lambda: WORKFLOW_RUNS_TOTAL.labels(workflow=workflow, status=status).inc())
    if duration_s is not None:
        _safe(lambda: WORKFLOW_DURATION_SECONDS.labels(workflow=workflow).observe(duration_s))


def observe_agent_node(agent: str, status: str, duration_s: float) -> None:
    _safe(
        lambda: AGENT_NODE_DURATION_SECONDS.labels(agent=agent, status=status).observe(duration_s)
    )


def observe_llm_call(
    model: str,
    *,
    status: str,
    mode: str,
    duration_s: float | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
) -> None:
    _safe(lambda: LLM_CALLS_TOTAL.labels(model=model, status=status, mode=mode).inc())
    if duration_s is not None:
        _safe(lambda: LLM_DURATION_SECONDS.labels(model=model).observe(duration_s))
    if tokens_in:
        _safe(lambda: LLM_TOKENS_TOTAL.labels(model=model, direction="in").inc(tokens_in))
    if tokens_out:
        _safe(lambda: LLM_TOKENS_TOTAL.labels(model=model, direction="out").inc(tokens_out))
    if cost_usd:
        _safe(lambda: LLM_COST_USD_TOTAL.labels(model=model).inc(cost_usd))


def observe_ingest_stage(stage: str, status: str, duration_s: float | None) -> None:
    if duration_s is not None:
        _safe(
            lambda: INGEST_STAGE_DURATION_SECONDS.labels(stage=stage, status=status).observe(
                duration_s
            )
        )
    if status == "failed":
        _safe(lambda: DOCUMENT_PROCESSING_FAILURES_TOTAL.labels(stage=stage).inc())


def observe_ml_inference(available: bool, duration_s: float) -> None:
    _safe(lambda: ML_INFERENCE_DURATION_SECONDS.observe(duration_s))
    _safe(lambda: ML_INFERENCE_TOTAL.labels(available=str(available).lower()).inc())


def observe_cam_generation(status: str, duration_s: float) -> None:
    _safe(lambda: CAM_GENERATION_DURATION_SECONDS.labels(status=status).observe(duration_s))


def observe_celery_task(
    task: str, status: str, *, duration_s: float | None = None, queue_latency_s: float | None = None
) -> None:
    # The tasks_total counter + duration histogram record a SETTLED task
    # (duration_s given, from task_postrun). A queue-latency-only call (from
    # task_prerun) must not also increment the settled-task counter.
    if duration_s is not None:
        _safe(lambda: CELERY_TASKS_TOTAL.labels(task=task, status=status).inc())
        _safe(
            lambda: CELERY_TASK_DURATION_SECONDS.labels(task=task, status=status).observe(
                duration_s
            )
        )
    if queue_latency_s is not None and queue_latency_s >= 0:
        _safe(lambda: CELERY_QUEUE_LATENCY_SECONDS.labels(task=task).observe(queue_latency_s))


def observe_db_query(operation: str, duration_s: float) -> None:
    _safe(lambda: DB_QUERY_DURATION_SECONDS.labels(operation=operation).observe(duration_s))


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
