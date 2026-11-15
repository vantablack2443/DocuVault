"""All SQL lives here. Functions take a session and never commit."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApiKey,
    AuditEntry,
    Policy,
    RefreshToken,
    RequestLog,
    Tenant,
    UsageDaily,
    User,
)

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


async def tenant_exists(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    return await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is not None


# --- audit log ---------------------------------------------------------------


async def lock_audit_chain(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Serialise appends per tenant until the transaction ends.

    Two writers reading the same "last entry" would both produce seq N+1. A row
    lock cannot help for an empty chain, so this is an advisory lock on the
    tenant id.
    """
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(str(tenant_id), 0)))
    )


async def last_audit_entry(session: AsyncSession, tenant_id: uuid.UUID) -> AuditEntry | None:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.tenant_id == tenant_id)
        .order_by(AuditEntry.seq.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def audit_source_exists(session: AsyncSession, source_id: str) -> bool:
    found = await session.scalar(select(AuditEntry.id).where(AuditEntry.source_id == source_id))
    return found is not None


async def list_audit(
    session: AsyncSession, tenant_id: uuid.UUID, *, limit: int, before_seq: int | None = None
) -> Sequence[AuditEntry]:
    statement = select(AuditEntry).where(AuditEntry.tenant_id == tenant_id)
    if before_seq is not None:
        statement = statement.where(AuditEntry.seq < before_seq)
    result = await session.execute(statement.order_by(AuditEntry.seq.desc()).limit(limit))
    return result.scalars().all()


async def audit_chain(session: AsyncSession, tenant_id: uuid.UUID) -> Sequence[AuditEntry]:
    result = await session.execute(
        select(AuditEntry).where(AuditEntry.tenant_id == tenant_id).order_by(AuditEntry.seq)
    )
    return result.scalars().all()


# --- usage and request metadata ----------------------------------------------


async def add_usage(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    day: date,
    *,
    requests: int,
    cache_hits: int,
    redactions: int,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Atomic increment: INSERT ... ON CONFLICT DO UPDATE SET n = n + excluded.n."""
    values = {
        "requests": requests,
        "cache_hits": cache_hits,
        "redactions": redactions,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    statement = insert(UsageDaily).values(tenant_id=tenant_id, day=day, **values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[UsageDaily.tenant_id, UsageDaily.day],
            set_={name: getattr(UsageDaily, name) + statement.excluded[name] for name in values},
        )
    )


async def list_usage(
    session: AsyncSession, tenant_id: uuid.UUID, since: date
) -> Sequence[UsageDaily]:
    result = await session.execute(
        select(UsageDaily)
        .where(UsageDaily.tenant_id == tenant_id, UsageDaily.day >= since)
        .order_by(UsageDaily.day)
    )
    return result.scalars().all()


async def list_requests(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int
) -> Sequence[RequestLog]:
    result = await session.execute(
        select(RequestLog)
        .where(RequestLog.tenant_id == tenant_id)
        .order_by(RequestLog.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
