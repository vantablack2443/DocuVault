import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import Principal
from app.core import security
from app.core.errors import NotFoundError
from app.db import repositories as repo
from app.db.models import ApiKey, User

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
        await session.commit()
        log.info("api_key_revoked", api_key_id=str(key_id), tenant_id=str(principal.tenant_id))
