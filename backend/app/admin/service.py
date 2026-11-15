import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.service import Principal
from app.core import security
from app.core.errors import NotFoundError
from app.db import repositories as repo
from app.db.models import ApiKey, RequestLog, UsageDaily, User
from app.redaction.types import PiiType

log = structlog.get_logger()


async def list_users(session: AsyncSession, principal: Principal) -> Sequence[User]:
    return await repo.list_users(session, principal.tenant_id)


async def list_api_keys(session: AsyncSession, principal: Principal) -> Sequence[ApiKey]:
    return await repo.list_api_keys(session, principal.tenant_id)


async def create_api_key(
    session: AsyncSession, principal: Principal, *, name: str
) -> tuple[ApiKey, str]:
    """Return the stored row and the plaintext key. The plaintext is never persisted."""
    generated = security.generate_api_key()
    api_key = ApiKey(
        tenant_id=principal.tenant_id,
        created_by=principal.user_id,
        name=name,
        prefix=generated.prefix,
        key_hash=generated.digest,
    )
    session.add(api_key)
    await session.flush()
    await audit.append(
        session,
        principal.tenant_id,
        actor=f"user:{principal.user_id}",
        action="api_key.created",
        details={"api_key_id": str(api_key.id), "prefix": api_key.prefix},
    )
    await session.commit()
    await session.refresh(api_key)
    log.info("api_key_created", api_key_id=str(api_key.id), tenant_id=str(principal.tenant_id))
    return api_key, generated.plaintext


async def revoke_api_key(session: AsyncSession, principal: Principal, key_id: uuid.UUID) -> None:
    api_key = await repo.get_api_key(session, principal.tenant_id, key_id)
    if api_key is None:
        raise NotFoundError
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        await audit.append(
            session,
            principal.tenant_id,
            actor=f"user:{principal.user_id}",
            action="api_key.revoked",
            details={"api_key_id": str(key_id)},
        )
        await session.commit()
        log.info("api_key_revoked", api_key_id=str(key_id), tenant_id=str(principal.tenant_id))


async def get_policy(session: AsyncSession, principal: Principal) -> list[PiiType]:
    stored = await repo.get_enabled_types(session, principal.tenant_id)
    return list(PiiType) if stored is None else [PiiType(name) for name in stored]


async def set_policy(
    session: AsyncSession, principal: Principal, enabled_types: Sequence[PiiType]
) -> list[PiiType]:
    # Stored in enum order and without duplicates, whatever the client sent.
    normalised = [pii_type for pii_type in PiiType if pii_type in set(enabled_types)]
    await repo.upsert_policy(
        session, principal.tenant_id, [pii_type.value for pii_type in normalised]
    )
    await audit.append(
        session,
        principal.tenant_id,
        actor=f"user:{principal.user_id}",
        action="policy.updated",
        details={"enabled_types": [pii_type.value for pii_type in normalised]},
    )
    await session.commit()
    log.info(
        "policy_updated",
        tenant_id=str(principal.tenant_id),
        enabled=[pii_type.value for pii_type in normalised],
    )
    return normalised


async def list_usage(
    session: AsyncSession, principal: Principal, days: int
) -> Sequence[UsageDaily]:
    since = (datetime.now(UTC) - timedelta(days=days - 1)).date()
    return await repo.list_usage(session, principal.tenant_id, since)


async def list_requests(
    session: AsyncSession, principal: Principal, limit: int
) -> Sequence[RequestLog]:
    return await repo.list_requests(session, principal.tenant_id, limit)
