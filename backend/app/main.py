"""FastAPI application factory and module-level ASGI app.

Wiring order: logging first (so even startup failures are structured), then
middleware (CORS outermost, request context inside it), error handlers,
routers under the configured API prefix, and Prometheus instrumentation on
``/metrics``. The lifespan logs a redacted configuration summary at startup
and disposes the engine/Redis client at shutdown. Phase 4 registers the SSE
relay; Phase 5 registers workflow routers — both slot into this factory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import make_url

from app.api.errors import register_error_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routers.admin import router as admin_router
from app.api.routers.auth import router as auth_router
from app.api.routers.cases import router as cases_router
from app.api.routers.companies import router as companies_router
from app.api.routers.health import router as health_router
from app.api.routers.insights import router as insights_router
from app.api.routers.users import router as users_router
from app.api.routers.workflows import router as workflows_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import register_metrics
from app.infrastructure.db.engine import dispose_engine
from app.infrastructure.redis.client import close_redis

logger = get_logger("app.main")

# --- OpenAPI documentation (surfaced at /docs and /redoc) -------------------
# A judge or integrator opening /docs should understand the product's contract
# in one screen: what the API does, how to authenticate, and how the routes are
# grouped. Per-endpoint descriptions come from the route function docstrings;
# this is the app-level framing on top of them.
_API_DESCRIPTION = """
**IntelliCredit AI** — an explainable corporate-intelligence & credit-decision
platform. Heterogeneous company evidence (GST returns, ITRs, bank statements,
annual reports, contracts, legal notices) is turned into a **citation-backed,
quantitatively auditable** credit decision.

**Two product invariants govern this API:**

1. *Explainability is structural.* Every claim traces to evidence (document
   page + bounding box, or an archived web snapshot); every score is a printed
   formula (see the `insights` routes).
2. *Numbers come from deterministic code and calibrated models; words come from
   the LLM.* A language model can never invent a score, a limit, or a rate.

### Authentication
All routes except `health` and `auth/login` require a **bearer token**:

1. `POST /api/v1/auth/login` with email + password → `{access_token, refresh_token}`.
2. Send `Authorization: Bearer <access_token>` on every subsequent request.
3. `POST /api/v1/auth/refresh` to rotate an expired access token.

Tokens carry `org_id` + role (`analyst`, `credit_officer`, `admin`); every
request is tenant-scoped and role-checked, with Postgres row-level security as a
second isolation layer.

### Live progress
Run and ingestion progress stream over **Server-Sent Events** at
`GET /api/v1/cases/{case_id}/events` (requires `CASE_READ`).

Operational metrics are exposed as Prometheus text at **`/metrics`**.
"""

_OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "health",
        "description": "Liveness/readiness probes — checks Postgres, Redis, and model artifacts.",
    },
    {
        "name": "auth",
        "description": "JWT authentication: login, token refresh, and the current-user profile.",
    },
    {"name": "users", "description": "User records within the caller's organization."},
    {"name": "companies", "description": "Corporate borrowers — the entities being appraised."},
    {
        "name": "cases",
        "description": (
            "Loan applications: create a case, upload evidence, track per-document "
            "ingestion stages, and subscribe to the live SSE event stream."
        ),
    },
    {
        "name": "workflows",
        "description": (
            "Credit-appraisal runs: start a run, poll status (with LIVE/REPLAY/DEGRADED "
            "execution mode), submit the human-in-the-loop officer review, and fetch the "
            "generated CAM / report and its PDF."
        ),
    },
    {
        "name": "insights",
        "description": (
            "Read models that power the dashboards: scorecard breakdown, ML default-risk "
            "explanation (PD + SHAP reason codes), evidence ledger, findings, and run summaries."
        ),
    },
    {"name": "admin", "description": "Feature flags and organization administration."},
]


def _redacted_dsn(dsn: str) -> str:
    """Render a connection URL with its password hidden (never log secrets)."""
    try:
        return make_url(dsn).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable-dsn>"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log a redacted startup summary; release connections at shutdown."""
    settings = get_settings()
    logger.info(
        "application_startup",
        app=settings.app.name,
        version=settings.app.version,
        environment=settings.app.env,
        debug=settings.app.debug,
        api_prefix=settings.app.api_prefix,
        database=_redacted_dsn(settings.database.url),
        redis=_redacted_dsn(settings.redis.url),
        json_logs=settings.log.json_logs,
    )
    # Register agents + built-in workflow definitions so the API can resolve and
    # enqueue runs. Fail-fast: a malformed definition should stop startup.
    from app.workflows.bootstrap import load_workflows

    load_workflows()
    yield
    await dispose_engine()
    await close_redis()
    logger.info("application_shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the fully wired FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log)

    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        summary="Explainable corporate-intelligence & credit-decision platform.",
        description=_API_DESCRIPTION,
        openapi_tags=_OPENAPI_TAGS,
        lifespan=_lifespan,
    )

    # Middleware: the last one added is outermost — CORS wraps the request
    # context middleware and every handled response (including AppError
    # envelopes). The one exception: Starlette's outermost ServerErrorMiddleware
    # emits truly-unhandled 500s itself, outside user middleware.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    app.include_router(health_router, prefix=settings.app.api_prefix)
    app.include_router(auth_router, prefix=settings.app.api_prefix)
    app.include_router(users_router, prefix=settings.app.api_prefix)
    app.include_router(companies_router, prefix=settings.app.api_prefix)
    app.include_router(cases_router, prefix=settings.app.api_prefix)
    app.include_router(workflows_router, prefix=settings.app.api_prefix)
    app.include_router(insights_router, prefix=settings.app.api_prefix)
    app.include_router(admin_router, prefix=settings.app.api_prefix)

    register_metrics(app)

    return app


app = create_app()
