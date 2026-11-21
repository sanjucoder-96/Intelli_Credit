"""Authentication flow: login, refresh, current user, token misuse."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import ANALYST, INACTIVE, TEST_PASSWORD


async def test_login_returns_token_pair(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": ANALYST.email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] != body["refresh_token"]
    assert body["expires_in"] > 0


async def test_login_wrong_password_is_401_envelope(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": ANALYST.email, "password": "wrong-password-1!"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_login_unknown_email_matches_wrong_password_message(client: AsyncClient) -> None:
    wrong_pw = await client.post(
        "/api/v1/auth/login",
        json={"email": ANALYST.email, "password": "wrong-password-1!"},
    )
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": TEST_PASSWORD},
    )
    assert unknown.status_code == wrong_pw.status_code == 401
    assert unknown.json()["error"]["message"] == wrong_pw.json()["error"]["message"]


async def test_login_inactive_user_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": INACTIVE.email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 401


async def test_refresh_roundtrip(client: AsyncClient) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": ANALYST.email, "password": TEST_PASSWORD},
    )
    refresh_token = login.json()["refresh_token"]

    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]


async def test_me_with_valid_token(client: AsyncClient, analyst_token: str) -> None:
    response = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {analyst_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == ANALYST.email
    assert body["role"] == "analyst"


async def test_me_without_token_is_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_refresh_token_rejected_as_access_token(client: AsyncClient) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": ANALYST.email, "password": TEST_PASSWORD},
    )
    refresh_token = login.json()["refresh_token"]

    response = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {refresh_token}"}
    )
    assert response.status_code == 401


async def test_garbage_token_is_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_validation_error_envelope(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["details"]
