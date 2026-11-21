"""Tavily web-research client (ARCHITECTURE §6) — honest, optional-key.

Without ``IC_TAVILY__API_KEY`` the client is *unconfigured*: search returns
no results and the Research/News agents record a ``gap`` finding rather than
fabricating citations. Every fetched source is snapshotted through the
StoragePort at claim time (ADR-0016 / risk R16) so link rot can never break
"walk me through it" months later. e-Courts and MCA are treated as cached
corpora, not pretend-live integrations (risk R9).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.application.ports import StoragePort
from app.core.config import TavilyConfig
from app.core.logging import get_logger

logger = get_logger("app.infrastructure.tavily")


class WebResult(BaseModel):
    """One web-search hit with its archived snapshot key."""

    title: str
    url: str
    content: str
    score: float = 0.0
    snapshot_key: str | None = None


class WebSearchResponse(BaseModel):
    configured: bool
    query: str
    results: list[WebResult] = Field(default_factory=list)


def _snapshot_key(url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return f"research/snapshots/{digest}.json"


class TavilyClient:
    """Async Tavily search with source snapshotting."""

    def __init__(self, config: TavilyConfig, storage: StoragePort | None = None) -> None:
        self._config = config
        self._storage = storage

    @property
    def configured(self) -> bool:
        return bool(self._config.api_key)

    async def search(self, query: str, *, max_results: int = 5) -> WebSearchResponse:
        """Search the web; archive each source; return typed results.

        Never raises on provider/network failure — research is best-effort and
        a failure becomes an empty result the agent reports as a gap.
        """
        if not self.configured:
            return WebSearchResponse(configured=False, query=query)

        payload = {
            "api_key": self._config.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                response = await client.post(f"{self._config.base_url}/search", json=payload)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
        except Exception as exc:  # provider/network failure -> empty, logged
            logger.warning("tavily_search_failed", error=str(exc))
            return WebSearchResponse(configured=True, query=query)

        results: list[WebResult] = []
        for item in data.get("results", [])[:max_results]:
            result = WebResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                content=str(item.get("content", "")),
                score=float(item.get("score", 0.0) or 0.0),
            )
            result.snapshot_key = await self._snapshot(result)
            results.append(result)
        return WebSearchResponse(configured=True, query=query, results=results)

    async def _snapshot(self, result: WebResult) -> str | None:
        """Archive the fetched source content at claim time (risk R16)."""
        if self._storage is None or not result.url:
            return None
        key = _snapshot_key(result.url)
        blob = json.dumps(
            {"url": result.url, "title": result.title, "content": result.content}
        ).encode()
        try:
            await self._storage.put_derived(key=key, data=blob)
            return key
        except Exception as exc:  # snapshot failure must not fail research
            logger.warning("tavily_snapshot_failed", url=result.url, error=str(exc))
            return None
