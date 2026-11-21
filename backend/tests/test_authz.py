"""Role/permission authorization via the test-only admin route."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import ADMIN, TEST_PASSWORD


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def test_admin_route_allows_admin(client: AsyncClient) -> None:
    token = await _login(client, ADMIN.email)
    response = await client.get(
        "/api/v1/_test/admin-only", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "granted"}


async def test_admin_route_blocks_analyst(client: AsyncClient, analyst_token: str) -> None:
    response = await client.get(
        "/api/v1/_test/admin-only", headers={"Authorization": f"Bearer {analyst_token}"}
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "forbidden"
    assert "admin" in body["error"]["details"]["required_roles"]


async def test_admin_route_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/_test/admin-only")
    assert response.status_code == 401
