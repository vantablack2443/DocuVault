"""Idempotent seed: one permanent tenant with an admin, for local runs.

Run with `python -m app.seed`. Does nothing unless both SEED_ADMIN_EMAIL and
SEED_ADMIN_PASSWORD are set.
"""

import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.service import normalise_email
from app.core import security
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db import repositories as repo
from app.db.models import Role, Tenant, User

log = structlog.get_logger()


async def seed(session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> bool:
    """Return True if the admin was created."""
    if settings.seed_admin_email is None or settings.seed_admin_password is None:
        log.info("seed_skipped", reason="SEED_ADMIN_EMAIL or SEED_ADMIN_PASSWORD not set")
        return False
    email = normalise_email(settings.seed_admin_email)
    async with session_factory() as session:
        if await repo.get_user_by_email(session, email) is not None:
            log.info("seed_skipped", reason="admin already exists")
            return False
        tenant = Tenant(name="Beispiel AG")
        session.add(tenant)
        await session.flush()
        session.add(
            User(
                tenant_id=tenant.id,
                email=email,
                role=Role.ADMIN,
                password_hash=security.hash_password(
                    settings.seed_admin_password.get_secret_value()
                ),
            )
        )
        await session.commit()
    log.info("seed_created_admin")
    return True


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_async_engine(settings.database_url)
    try:
        await seed(async_sessionmaker(engine, expire_on_commit=False), settings)
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
