"""All SQL lives here. Functions take a session and never commit."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Policy, RefreshToken, Tenant, User

# --- users and tenants -------------------------------------------------------


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def get_user_with_tenant(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[User, Tenant] | None:
    row = (
        await session.execute(
            select(User, Tenant).join(Tenant, User.tenant_id == Tenant.id).where(User.id == user_id)
        )
    ).one_or_none()
    return (row[0], row[1]) if row else None


async def list_users(session: AsyncSession, tenant_id: uuid.UUID) -> Sequence[User]:
    result = await session.execute(
        select(User).where(User.tenant_id == tenant_id).order_by(User.created_at, User.email)
    )
    return result.scalars().all()


async def delete_expired_guest_tenants(session: AsyncSession, now: datetime) -> None:
    """Users, tokens and keys go with the tenant through ON DELETE CASCADE."""
    await session.execute(delete(Tenant).where(Tenant.is_guest, Tenant.expires_at < now))


# --- refresh tokens ----------------------------------------------------------


async def get_refresh_token_for_update(
    session: AsyncSession, token_hash: str
) -> RefreshToken | None:
    """Row lock, so two concurrent refreshes cannot both rotate the same token."""
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    return result.scalar_one_or_none()


async def revoke_all_refresh_tokens(
    session: AsyncSession, user_id: uuid.UUID, now: datetime
) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


# --- api keys ----------------------------------------------------------------


async def list_api_keys(session: AsyncSession, tenant_id: uuid.UUID) -> Sequence[ApiKey]:
    result = await session.execute(
        select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc())
    )
    return result.scalars().all()


async def get_api_key(
    session: AsyncSession, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> ApiKey | None:
    result = await session.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def get_api_key_with_tenant_by_prefix(
    session: AsyncSession, prefix: str
) -> tuple[ApiKey, Tenant] | None:
    row = (
        await session.execute(
            select(ApiKey, Tenant)
            .join(Tenant, ApiKey.tenant_id == Tenant.id)
            .where(ApiKey.prefix == prefix)
        )
    ).one_or_none()
    return (row[0], row[1]) if row else None


# --- policies ----------------------------------------------------------------


async def get_enabled_types(session: AsyncSession, tenant_id: uuid.UUID) -> list[str] | None:
    """`None` means the tenant has no policy row and every PII type is redacted."""
    result = await session.execute(
        select(Policy.enabled_types).where(Policy.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def upsert_policy(
    session: AsyncSession, tenant_id: uuid.UUID, enabled_types: list[str]
) -> None:
    statement = insert(Policy).values(tenant_id=tenant_id, enabled_types=enabled_types)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[Policy.tenant_id],
            set_={"enabled_types": enabled_types, "updated_at": func.now()},
        )
    )
