"""Repositories: tenant-scoped persistence adapters behind application ports.

Every statement — tenant-scoped or deliberately cross-org (login) — is built
through :meth:`BaseRepository.scoped`, the single ``org_id``/soft-delete
choke point (ADR-0013). Phase 3 adds document/chunk repositories; Phase 5
adds workflow-execution and evidence repositories on the same base.
"""

from app.infrastructure.db.repositories.base import BaseRepository
from app.infrastructure.db.repositories.companies import CompanyRepository
from app.infrastructure.db.repositories.feature_flags import FeatureFlagRepository
from app.infrastructure.db.repositories.users import UserRepository

__all__ = [
    "BaseRepository",
    "CompanyRepository",
    "FeatureFlagRepository",
    "UserRepository",
]
