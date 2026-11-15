"""Tamper-evident audit log: every entry commits to the one before it."""

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models import AuditEntry

GENESIS_HASH = "0" * 64


def entry_hash(
    *,
    prev_hash: str,
    tenant_id: uuid.UUID,
    seq: int,
    actor: str,
    action: str,
    details: dict[str, object],
    created_at: datetime,
) -> str:
    """SHA-256 over a canonical encoding of the entry and its predecessor's hash.

    Changing, removing or reordering any entry changes every hash after it.
    """
    canonical = json.dumps(
        [
            prev_hash,
            str(tenant_id),
            seq,
            actor,
            action,
            details,
            created_at.astimezone(UTC).isoformat(),
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def append(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    actor: str,
    action: str,
    details: dict[str, object] | None = None,
    source_id: str | None = None,
) -> AuditEntry | None:
    """Add an entry inside the caller's transaction (the caller commits).

    So the entry and the change it describes are saved together or not at all.
    Returns `None` if `source_id` was already recorded (a redelivered event).
    `details` must never contain personal data: ids, counts and enum values only.
    """
    await repo.lock_audit_chain(session, tenant_id)
    if source_id is not None and await repo.audit_source_exists(session, source_id):
        return None
    last = await repo.last_audit_entry(session, tenant_id)
    seq = last.seq + 1 if last else 1
    prev_hash = last.hash if last else GENESIS_HASH
    created_at = datetime.now(UTC)
    details = details or {}
    entry = AuditEntry(
        tenant_id=tenant_id,
        seq=seq,
        actor=actor,
        action=action,
        details=details,
        created_at=created_at,
        prev_hash=prev_hash,
        hash=entry_hash(
            prev_hash=prev_hash,
            tenant_id=tenant_id,
            seq=seq,
            actor=actor,
            action=action,
            details=details,
            created_at=created_at,
        ),
        source_id=source_id,
    )
    session.add(entry)
    await session.flush()
    return entry


@dataclass(frozen=True, slots=True)
class Verification:
    valid: bool
    entries: int
    first_invalid_seq: int | None


def verify_chain(tenant_id: uuid.UUID, chain: Sequence[AuditEntry]) -> Verification:
    """Recompute every hash. Reports the first entry that does not fit."""
    prev_hash = GENESIS_HASH
    for position, entry in enumerate(chain, start=1):
        expected = entry_hash(
            prev_hash=prev_hash,
            tenant_id=tenant_id,
            seq=position,
            actor=entry.actor,
            action=entry.action,
            details=entry.details,
            created_at=entry.created_at,
        )
        if entry.seq != position or entry.prev_hash != prev_hash or entry.hash != expected:
            return Verification(False, len(chain), entry.seq)
        prev_hash = entry.hash
    return Verification(True, len(chain), None)


async def verify(session: AsyncSession, tenant_id: uuid.UUID) -> Verification:
    return verify_chain(tenant_id, await repo.audit_chain(session, tenant_id))


async def list_entries(
    session: AsyncSession, tenant_id: uuid.UUID, *, limit: int, before_seq: int | None
) -> Sequence[AuditEntry]:
    return await repo.list_audit(session, tenant_id, limit=limit, before_seq=before_seq)
