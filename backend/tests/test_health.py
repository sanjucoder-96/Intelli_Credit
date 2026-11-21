"""Health/readiness endpoint behaviour (no live services required)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["environment"] == "testing"


async def test_ready_ok_when_dependencies_up(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def up() -> bool:
        return True

    monkeypatch.setattr("app.api.routers.health.ping_database", up)
    monkeypatch.setattr("app.api.routers.health.ping_redis", up)

    response = await client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": True, "redis": True}


async def test_ready_503_when_database_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down() -> bool:
        return False

    async def up() -> bool:
        return True

    monkeypatch.setattr("app.api.routers.health.ping_database", down)
    monkeypatch.setattr("app.api.routers.health.ping_redis", up)

    response = await client.get("/api/v1/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "service_unavailable"
    assert body["error"]["details"]["checks"]["database"] is False
    assert body["error"]["request_id"]


async def test_metrics_endpoint_exposed(client: AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "python_info" in response.text or "http" in response.text
