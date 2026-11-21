"""Seed the development database: org, one user per role, companies, cases.

Idempotent — re-running updates nothing and creates nothing that already
exists (keyed on slugs/emails). Also bootstraps the RLS application role so
``tenant_session`` works immediately after seeding (ADR-0013).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import PasswordHasher
from app.domain.enums import DataOrigin, LoanApplicationStatus, Role
from app.infrastructure.db.engine import create_engine_and_factory
from app.infrastructure.db.models import Company, LoanApplication, Organization, User
from app.infrastructure.db.rls import ensure_app_role

logger = get_logger("app.seeds.demo")

DEMO_ORG_SLUG = "demo-lender"
DEMO_PASSWORD = "Demo!Pass123"  # noqa: S105 - synthetic demo credential, printed on seed

DEMO_USERS: tuple[tuple[str, str, Role], ...] = (
    ("analyst@demo.intellicredit.in", "Asha Analyst", Role.ANALYST),
    ("officer@demo.intellicredit.in", "Omar Officer", Role.CREDIT_OFFICER),
    ("admin@demo.intellicredit.in", "Arjun Admin", Role.ADMIN),
)

DEMO_COMPANIES: tuple[tuple[str, str, str], ...] = (
    # (legal name, sector, GSTIN) — identifiers are format-valid but synthetic.
    ("Acme Textiles Private Limited", "Textiles", "27AAACA1234A1Z5"),
    ("Deccan Agro Foods Limited", "Food Processing", "36AABCD5678B1Z3"),
    ("Nimbus Logistics Private Limited", "Logistics", "29AACCN9012C1Z8"),
)


async def _get_or_create_org(session: AsyncSession) -> Organization:
    org = (
        await session.execute(sa.select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    ).scalar_one_or_none()
    if org is None:
        org = Organization(name="Demo Lender (Synthetic)", slug=DEMO_ORG_SLUG, is_active=True)
        session.add(org)
        await session.flush()
        logger.info("seeded_organization", slug=DEMO_ORG_SLUG)
    return org


async def _get_or_create_users(session: AsyncSession, org_id: UUID) -> int:
    hasher = PasswordHasher()
    created = 0
    for email, full_name, role in DEMO_USERS:
        exists = (
            await session.execute(sa.select(User.id).where(User.email == email))
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                User(
                    org_id=org_id,
                    email=email,
                    full_name=full_name,
                    hashed_password=hasher.hash(DEMO_PASSWORD),
                    role=role,
                    is_active=True,
                )
            )
            created += 1
    return created


async def _get_or_create_companies(session: AsyncSession, org_id: UUID) -> list[Company]:
    companies: list[Company] = []
    for legal_name, sector, gstin in DEMO_COMPANIES:
        company = (
            await session.execute(
                sa.select(Company).where(Company.org_id == org_id, Company.legal_name == legal_name)
            )
        ).scalar_one_or_none()
        if company is None:
            company = Company(
                org_id=org_id,
                legal_name=legal_name,
                sector=sector,
                gstin=gstin,
                data_origin=DataOrigin.SYNTHETIC_DEMO,
            )
            session.add(company)
            await session.flush()
        companies.append(company)
    return companies


async def _get_or_create_cases(
    session: AsyncSession, org_id: UUID, companies: list[Company]
) -> int:
    created = 0
    for company in companies[:2]:
        title = f"Working-capital appraisal — {company.legal_name}"
        exists = (
            await session.execute(
                sa.select(LoanApplication.id).where(
                    LoanApplication.org_id == org_id, LoanApplication.title == title
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                LoanApplication(
                    org_id=org_id,
                    company_id=company.id,
                    title=title,
                    status=LoanApplicationStatus.DRAFT,
                    currency="INR",
                )
            )
            created += 1
    return created


async def seed() -> None:
    """Seed the demo tenant and bootstrap the RLS application role."""
    settings = get_settings()
    engine, factory = create_engine_and_factory(settings.database)
    try:
        await ensure_app_role(engine)
        async with factory() as session:
            org = await _get_or_create_org(session)
            users_created = await _get_or_create_users(session, org.id)
            companies = await _get_or_create_companies(session, org.id)
            cases_created = await _get_or_create_cases(session, org.id, companies)
            await session.commit()
        logger.info(
            "demo_seed_complete",
            org=DEMO_ORG_SLUG,
            users_created=users_created,
            companies=len(companies),
            cases_created=cases_created,
        )
        print(  # noqa: T201 - CLI output by design
            "Seeded demo tenant "
            f"'{DEMO_ORG_SLUG}' — users: "
            + ", ".join(email for email, _, _ in DEMO_USERS)
            + f" (password: {DEMO_PASSWORD}) — all data SYNTHETIC_DEMO."
        )
    finally:
        await engine.dispose()


def main() -> None:
    """CLI entry point: ``python -m app.seeds.demo``."""
    configure_logging(get_settings().log)
    asyncio.run(seed())


if __name__ == "__main__":
    main()
