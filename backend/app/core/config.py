"""Application configuration via nested pydantic-settings.

All configuration is environment-driven (``IC_`` prefix, ``__`` nested
delimiter, optional ``.env`` file) and validated fail-fast at boot
(ARCHITECTURE §14). Phase 4 adds Celery worker tuning knobs; Phase 5 adds
LLM-gateway settings — both as new sub-models here.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseModel):
    """General application identity and HTTP surface configuration."""

    env: Literal["development", "testing", "production"] = "development"
    debug: bool = False
    name: str = "IntelliCredit"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    version: str = "0.2.0"


class DatabaseConfig(BaseModel):
    """Async PostgreSQL connection settings (postgresql+asyncpg DSNs).

    ``url`` is the admin/owner connection (migrations, pre-auth paths, system
    jobs). ``rls_url`` is the non-superuser application role subject to
    row-level security — tenant sessions require it (ADR-0013); see
    ``app.infrastructure.db.rls.ensure_app_role``.
    """

    url: str
    rls_url: str | None = None
    echo: bool = False
    pool_size: int = 10
    max_overflow: int = 20


class RedisConfig(BaseModel):
    """Redis connection settings (cache, rate limits, Celery broker backing)."""

    url: str


class AuthConfig(BaseModel):
    """JWT signing and token lifetime settings (ADR-0013)."""

    jwt_secret: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    access_ttl_seconds: int = 900
    refresh_ttl_seconds: int = 1209600


class CeleryConfig(BaseModel):
    """Celery broker/result configuration and queue topology (ADR-0004)."""

    broker_url: str
    result_backend: str
    default_queue: str = "agents"
    queues: list[str] = Field(default_factory=lambda: ["agents", "ingest", "ocr"])


class LogConfig(BaseModel):
    """Structured logging configuration (structlog, JSON in production)."""

    level: str = "INFO"
    json_logs: bool = Field(default=True, alias="json")

    model_config = {"populate_by_name": True}


class StorageConfig(BaseModel):
    """Blob storage settings for the local-filesystem StoragePort backend
    (ADR-0016). ``root`` is a Docker volume in compose."""

    root: str = "./storage"


class GeminiConfig(BaseModel):
    """Gemini API settings (ADR-0005 model tiering).

    ``api_key`` is optional: without it, LLM-dependent steps park with a
    machine-readable ``gemini_unconfigured`` error instead of running — never
    silently faked (deterministic extractors still run).

    Model defaults track the current Gemini generation: the 2.5 line the ADRs
    were written against has been retired for new keys, so the Flash tier is
    ``gemini-3.7-flash`` (latest stable) and the escalation tier is
    ``gemini-3.1-pro-preview`` (the current Pro). Override per environment via
    ``IC_GEMINI__FLASH_MODEL`` / ``IC_GEMINI__PRO_MODEL``.
    """

    api_key: str | None = None
    flash_model: str = "gemini-3.7-flash"
    pro_model: str = "gemini-3.1-pro-preview"


class TavilyConfig(BaseModel):
    """Web-research provider settings (ARCHITECTURE §6).

    ``api_key`` is optional: without it the research tools return no results
    and the Research/News agents record a ``gap`` finding — honest best-effort,
    never fabricated citations. e-Courts/MCA remain cached-corpus concerns.
    """

    api_key: str | None = None
    base_url: str = "https://api.tavily.com"
    timeout_seconds: float = 20.0


class AgentConfig(BaseModel):
    """Agent-runtime settings (ADR-0005)."""

    llm_mode: Literal["live", "record", "replay"] = "live"
    run_cost_cap_usd: float = 2.0
    rate_limit_per_sec: float = 8.0
    # Retries for transient provider failures (503 "high demand", 429) before a
    # call gives up — keeps a demo robust to temporary Gemini unavailability.
    llm_max_retries: int = 3


class MlConfig(BaseModel):
    """ML inference settings (ADR-0010).

    The PD model is served in-process: the champion artifact is loaded from
    ``artifact_dir`` at first use and cached. ``enabled=False`` (or no champion
    registered, or feature coverage below ``min_feature_coverage``) keeps the
    ml_default_risk pillar excluded, so the deterministic scorecard degrades
    exactly as it did before Phase 6 — the coverage mechanism is preserved.
    """

    enabled: bool = True
    artifact_dir: str = "./storage/models"
    champion_model_name: str = "pd_model"
    min_feature_coverage: float = 0.6


class IngestConfig(BaseModel):
    """Ingestion pipeline guardrails (ARCHITECTURE §7 / §15.2)."""

    max_upload_mb: int = 50
    zip_max_depth: int = 3
    zip_max_total_mb: int = 200
    ocr_text_density_threshold: float = 0.05  # chars/page-area score below -> needs OCR
    ocr_line_confidence_threshold: float = 0.80  # mean line conf below -> vision escalation
    page_render_dpi: int = 220
    chunk_target_tokens: int = 500
    chunk_overlap_ratio: float = 0.15


class Settings(BaseSettings):
    """Root settings object; construct via :func:`get_settings`.

    Environment variables use the ``IC_`` prefix with ``__`` as the nested
    delimiter, e.g. ``IC_DATABASE__URL`` or ``IC_AUTH__JWT_SECRET``.
    """

    app: AppConfig = Field(default_factory=AppConfig)
    database: DatabaseConfig
    redis: RedisConfig
    auth: AuthConfig
    celery: CeleryConfig
    log: LogConfig = Field(default_factory=LogConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    tavily: TavilyConfig = Field(default_factory=TavilyConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    ml: MlConfig = Field(default_factory=MlConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)

    model_config = SettingsConfigDict(
        env_prefix="IC_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )


class SettingsError(RuntimeError):
    """Raised when required configuration is missing or invalid at boot."""


# Convenience fallbacks for the Gemini key: the canonical variable is
# ``IC_GEMINI__API_KEY``, but a plain key under any of these common names is
# picked up too, so a key dropped into ``.env`` "just works".
_GEMINI_KEY_FALLBACKS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API")


@lru_cache
def get_settings() -> Settings:
    """Return the cached process-wide :class:`Settings`.

    Fails fast with a :class:`SettingsError` that lists every missing or
    invalid environment variable so misconfiguration is obvious at startup.
    """
    try:
        settings = Settings()
    except ValidationError as exc:
        missing = ", ".join(
            "IC_" + "__".join(str(part).upper() for part in err["loc"]) for err in exc.errors()
        )
        raise SettingsError(
            f"Invalid or missing configuration. Check environment variables: {missing}"
        ) from exc

    if not settings.gemini.api_key:
        for name in _GEMINI_KEY_FALLBACKS:
            value = os.environ.get(name)
            if value:
                settings.gemini.api_key = value
                break
    return settings
