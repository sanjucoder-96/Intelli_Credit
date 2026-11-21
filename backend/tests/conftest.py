"""Shared test fixtures.

Unit tests run with NO live services: required configuration is provided via
environment defaults below (set before any ``app.*`` import), DB-backed
providers are overridden with in-memory fakes, and readiness pings are
monkeypatched. Integration tests (``-m integration``) expect live
Postgres/Redis (compose test profile or CI services) via ``IC_DATABASE__URL``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator

# The durable checkpointer uses psycopg (libpq), which on Windows cannot run on
# the default ProactorEventLoop — it requires a SelectorEventLoop. asyncpg works
# on either, so selecting the selector policy for the whole test session is safe
# and is what production Linux already uses (epoll is selector-based).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

# Environment must be complete before the first app import: Settings is
# validated fail-fast and the Celery app builds from it at import time.
_TEST_ENV = {
    "IC_APP__ENV": "testing",
    "IC_DATABASE__URL": "postgresql+asyncpg://intellicredit:intellicredit@127.0.0.1:5432/intellicredit_test",
    "IC_REDIS__URL": "redis://127.0.0.1:6379/0",
    "IC_AUTH__JWT_SECRET": "unit-test-secret-at-least-16-chars",
    "IC_CELERY__BROKER_URL": "redis://127.0.0.1:6379/1",
    "IC_CELERY__RESULT_BACKEND": "redis://127.0.0.1:6379/2",
    "IC_LOG__JSON": "false",
}
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

import pytest  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api import providers  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.security import PasswordHasher, TokenService  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.main import create_app  # noqa: E402

get_settings.cache_clear()

_HASHER = PasswordHasher()
TEST_PASSWORD = "password123!"
_TEST_PASSWORD_HASH = _HASHER.hash(TEST_PASSWORD)


@dataclass
class FakeUserRow:
    """Duck-typed stand-in for the ``users`` ORM row."""

    id: UUID
    org_id: UUID
    email: str
    full_name: str
    hashed_password: str
    role: Role
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_login_at: datetime | None = None


class FakeUserRepository:
    """In-memory ``UserRepositoryPort`` implementation."""

    def __init__(self, users: list[FakeUserRow]) -> None:
        self._by_email = {user.email: user for user in users}
        self._by_id = {user.id: user for user in users}

    async def get_by_email(self, email: str) -> FakeUserRow | None:
        return self._by_email.get(email)

    async def get_by_id(self, user_id: UUID) -> FakeUserRow | None:
        return self._by_id.get(user_id)

    async def get(self, ctx: object, user_id: UUID) -> FakeUserRow | None:
        """Tenant-scoped lookup mirroring ``UserRepository.get``."""
        user = self._by_id.get(user_id)
        org_id = getattr(ctx, "org_id", None)
        if user is None or (org_id is not None and user.org_id != org_id):
            return None
        return user

    async def touch_last_login(self, user_id: UUID) -> None:
        user = self._by_id.get(user_id)
        if user is not None:
            user.last_login_at = datetime.now(UTC)


ORG_ID = uuid4()

ANALYST = FakeUserRow(
    id=uuid4(),
    org_id=ORG_ID,
    email="analyst@example.com",
    full_name="Asha Analyst",
    hashed_password=_TEST_PASSWORD_HASH,
    role=Role.ANALYST,
)
ADMIN = FakeUserRow(
    id=uuid4(),
    org_id=ORG_ID,
    email="admin@example.com",
    full_name="Arjun Admin",
    hashed_password=_TEST_PASSWORD_HASH,
    role=Role.ADMIN,
)
INACTIVE = FakeUserRow(
    id=uuid4(),
    org_id=ORG_ID,
    email="inactive@example.com",
    full_name="Ira Inactive",
    hashed_password=_TEST_PASSWORD_HASH,
    role=Role.ANALYST,
    is_active=False,
)


@pytest.fixture
def settings() -> Settings:
    """Validated test settings (env provided at module import)."""
    return get_settings()


@pytest.fixture
def fake_user_repo() -> FakeUserRepository:
    """Fresh in-memory user repository per test."""
    return FakeUserRepository(
        [
            FakeUserRow(**vars(ANALYST)),
            FakeUserRow(**vars(ADMIN)),
            FakeUserRow(**vars(INACTIVE)),
        ]
    )


@pytest.fixture
def app(fake_user_repo: FakeUserRepository) -> FastAPI:
    """The real application with DB-backed providers overridden.

    A test-only admin route is mounted so authorization behaviour is
    exercised without any business endpoint existing yet.
    """
    application = create_app()
    application.dependency_overrides[providers.get_user_repository] = lambda: fake_user_repo
    # Unit tests never touch a real database: the admin session becomes an
    # inert stub (audit writes vanish — integration tests cover them), and the
    # tenant session fails loudly if any route unexpectedly reaches for it.
    application.dependency_overrides[providers.get_db_session] = _stub_session
    application.dependency_overrides[providers.get_tenant_db_session] = _forbidden_session

    @application.get(
        "/api/v1/_test/admin-only",
        dependencies=[Depends(providers.require_roles(Role.ADMIN))],
    )
    async def admin_only() -> dict[str, str]:
        return {"status": "granted"}

    return application


class _StubDbSession:
    """Inert stand-in for the admin session: absorbs audit writes in unit tests."""

    def add(self, instance: object) -> None:  # noqa: ARG002 - intentional no-op
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


async def _stub_session() -> AsyncIterator[_StubDbSession]:
    yield _StubDbSession()


async def _forbidden_session() -> AsyncIterator[None]:
    """Dependency override: unit tests must never open a tenant DB session."""
    raise AssertionError("unit tests must not open a tenant database session")
    yield  # pragma: no cover - generator shape required by FastAPI


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async HTTP client bound to the in-process ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def token_service(settings: Settings) -> TokenService:
    """Token service sharing the app's test secret."""
    return TokenService(settings.auth)


@pytest.fixture
async def analyst_token(client: AsyncClient) -> str:
    """Access token minted through the real login endpoint."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": ANALYST.email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])
