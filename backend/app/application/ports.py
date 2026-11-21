"""Ports (typing.Protocol) the application layer depends on.

Type-only structural contracts; concrete adapters live in
``app.infrastructure`` and ``app.core.security`` and are bound in
``app.api.providers`` (ADR-0001). Phase 3+ adds StoragePort, LLMGatewayPort,
OcrPort, and RetrieverPort here as their consumers land.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.context import TenantContext
from app.core.security import TokenPayload
from app.domain.entities import CompanyEntity
from app.domain.enums import Role


class UserRow(BaseModel):
    """Full user record needed by auth use cases (includes password hash).

    Distinct from :class:`app.domain.entities.UserEntity`, which is the
    outward-facing read model and never carries the hash.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    hashed_password: str
    last_login_at: datetime | None = None
    created_at: datetime


class UserRepositoryPort(Protocol):
    """Persistence port for users."""

    async def get_by_email(self, email: str) -> UserRow | None:
        """Cross-org lookup by unique email (login path only)."""
        ...

    async def get(self, ctx: TenantContext, user_id: UUID) -> UserRow | None:
        """Tenant-scoped lookup by primary key."""
        ...

    async def get_by_id(self, user_id: UUID) -> UserRow | None:
        """Cross-org lookup by primary key (token refresh/introspection only).

        Used before a :class:`TenantContext` exists — the org claim is derived
        from the row itself, mirroring ``get_by_email`` on the login path.
        """
        ...

    async def touch_last_login(self, user_id: UUID) -> None:
        """Record a successful login timestamp."""
        ...


class CompanyRepositoryPort(Protocol):
    """Persistence port for borrower companies (skeleton; grows in Phase 3)."""

    async def get(self, ctx: TenantContext, company_id: UUID) -> CompanyEntity | None:
        """Tenant-scoped lookup by primary key."""
        ...

    async def list(
        self, ctx: TenantContext, *, limit: int = 50, offset: int = 0
    ) -> list[CompanyEntity]:
        """Tenant-scoped paginated listing."""
        ...


class FeatureFlagPort(Protocol):
    """Typed feature-flag accessor (ADR-0015)."""

    async def is_on(self, key: str, ctx: TenantContext | None = None) -> bool:
        """Resolve a flag: env override > org override > global default."""
        ...


class StoredBlob(BaseModel):
    """Result of storing bytes through the StoragePort."""

    key: str
    sha256: str
    size: int


class StoragePort(Protocol):
    """Content-addressed blob storage (ADR-0016).

    Local-filesystem backend now; an S3/MinIO adapter is a config swap when
    multi-host arrives. The malware-scan hook wraps ``put`` (Phase 4 ships a
    size/type guard; an AV integration point is documented, not faked).
    """

    async def put(self, *, case_id: UUID | None, filename: str, data: bytes) -> StoredBlob:
        """Store bytes content-addressed under the case scope."""
        ...

    async def put_derived(self, *, key: str, data: bytes) -> StoredBlob:
        """Store pipeline-derived bytes at an explicit deterministic key."""
        ...

    async def get(self, key: str) -> bytes:
        """Read a blob fully into memory (raises NotFoundError if absent)."""
        ...

    async def exists(self, key: str) -> bool:
        """Whether a blob exists at ``key``."""
        ...


class EmbeddingProviderPort(Protocol):
    """Text-embedding provider (ADR-0007: fastembed default, Gemini swap)."""

    @property
    def dimension(self) -> int:
        """Output vector dimension (must match the chunks column)."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into unit vectors."""
        ...


class SourceConnectorPort(Protocol):
    """Where evidence comes from (ARCHITECTURE §4: upload is connector #1;
    a Databricks/lakehouse/API feed is a new connector, not a rewrite)."""

    @property
    def name(self) -> str:
        """Connector identifier recorded on ingested documents."""
        ...

    async def fetch(self) -> list[tuple[str, bytes]]:
        """Pull pending (filename, content) pairs from the source."""
        ...


class PasswordHasherPort(Protocol):
    """Structural match for :class:`app.core.security.PasswordHasher`."""

    def hash(self, password: str) -> str: ...

    def verify(self, hashed: str, password: str) -> bool: ...


class TokenServicePort(Protocol):
    """Structural match for :class:`app.core.security.TokenService`."""

    def create_access_token(self, sub: UUID, org: UUID, role: str) -> str: ...

    def create_refresh_token(self, sub: UUID) -> str: ...

    def decode(self, token: str, expected_type: Literal["access", "refresh"]) -> TokenPayload: ...
