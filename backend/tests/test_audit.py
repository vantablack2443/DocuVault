import asyncio
import itertools
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.db.models import AuditEntry, Role, Tenant
from tests.factories import PASSWORD, auth_headers, create_tenant, create_user


async def _append(session: AsyncSession, tenant: Tenant, action: str, **details: object) -> None:
    await audit.append(session, tenant.id, actor="user:test", action=action, details=details)
    await session.commit()


# --- hash chain --------------------------------------------------------------


def test_hash_is_deterministic_and_sensitive_to_every_field() -> None:
    base: dict[str, object] = {
        "prev_hash": audit.GENESIS_HASH,
        "tenant_id": uuid.UUID(int=1),
        "seq": 1,
        "actor": "user:a",
        "action": "policy.updated",
        "details": {"b": 1, "a": [1, 2]},
        "created_at": datetime(2026, 11, 15, 12, 0, tzinfo=UTC),
    }
    reference = audit.entry_hash(**base)  # type: ignore[arg-type]

    assert reference == audit.entry_hash(**{**base, "details": {"a": [1, 2], "b": 1}})  # type: ignore[arg-type]
    changes: list[dict[str, object]] = [
        {"prev_hash": "1" * 64},
        {"tenant_id": uuid.UUID(int=2)},
        {"seq": 2},
        {"actor": "user:b"},
        {"action": "policy.deleted"},
        {"details": {"b": 2, "a": [1, 2]}},
        {"created_at": datetime(2026, 11, 15, 12, 0, 0, 1, tzinfo=UTC)},
    ]
    hashes = {audit.entry_hash(**{**base, **change}) for change in changes}  # type: ignore[arg-type]
    assert reference not in hashes and len(hashes) == len(changes)


async def test_entries_form_a_chain_that_verifies(session: AsyncSession) -> None:
    tenant = await create_tenant(session)
    assert await audit.verify(session, tenant.id) == audit.Verification(True, 0, None)

    for index in range(4):
        await _append(session, tenant, "test.event", index=index, umlaut="Müller")

    chain = (
        await session.scalars(
            select(AuditEntry).where(AuditEntry.tenant_id == tenant.id).order_by(AuditEntry.seq)
        )
    ).all()
    assert [entry.seq for entry in chain] == [1, 2, 3, 4]
    assert chain[0].prev_hash == audit.GENESIS_HASH
    assert all(later.prev_hash == earlier.hash for earlier, later in itertools.pairwise(chain))
    assert await audit.verify(session, tenant.id) == audit.Verification(True, 4, None)


async def test_concurrent_appends_get_distinct_sequence_numbers(
    app: FastAPI, session: AsyncSession
) -> None:
    tenant = await create_tenant(session)

    async def writer(index: int) -> None:
        async with app.state.session_factory() as own:
            await audit.append(
                own, tenant.id, actor="user:test", action="race", details={"i": index}
            )
            await own.commit()

    await asyncio.gather(*(writer(index) for index in range(12)))

    sequence = (
        await session.scalars(
            select(AuditEntry.seq).where(AuditEntry.tenant_id == tenant.id).order_by(AuditEntry.seq)
        )
    ).all()
    assert sequence == list(range(1, 13))
    assert (await audit.verify(session, tenant.id)).valid


async def test_same_source_event_is_recorded_once(session: AsyncSession) -> None:
    tenant = await create_tenant(session)
    source = f"{uuid.uuid4().int % 10**12}-0"

    first = await audit.append(session, tenant.id, actor="key:x", action="e", source_id=source)
    second = await audit.append(session, tenant.id, actor="key:x", action="e", source_id=source)
    await session.commit()

    assert first is not None and second is None


# --- immutability ------------------------------------------------------------


async def test_database_rejects_update_and_delete(session: AsyncSession) -> None:
    tenant = await create_tenant(session)
    await _append(session, tenant, "test.event")
    tenant_id = tenant.id  # a rollback expires loaded objects

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(
            update(AuditEntry).where(AuditEntry.tenant_id == tenant_id).values(actor="user:evil")
        )
    await session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(delete(AuditEntry).where(AuditEntry.tenant_id == tenant_id))
    await session.rollback()

    assert (await audit.verify(session, tenant_id)).valid


async def test_entries_leave_only_together_with_their_tenant(session: AsyncSession) -> None:
    tenant = await create_tenant(session)
    await _append(session, tenant, "test.event")

    await session.execute(delete(Tenant).where(Tenant.id == tenant.id))
    await session.commit()

    left = await session.scalar(
        select(func.count()).select_from(AuditEntry).where(AuditEntry.tenant_id == tenant.id)
    )
    assert left == 0


async def test_verification_pinpoints_a_tampered_entry(session: AsyncSession) -> None:
    """Someone with database superpowers can switch the trigger off. The chain still tells."""
    tenant = await create_tenant(session)
    for index in range(5):
        await _append(session, tenant, "test.event", index=index)

    await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable"))
    try:
        await session.execute(
            update(AuditEntry)
            .where(AuditEntry.tenant_id == tenant.id, AuditEntry.seq == 3)
            .values(details={"index": 999})
        )
        await session.commit()
        assert await audit.verify(session, tenant.id) == audit.Verification(False, 5, 3)

        await session.execute(
            delete(AuditEntry).where(AuditEntry.tenant_id == tenant.id, AuditEntry.seq == 2)
        )
        await session.commit()
        assert await audit.verify(session, tenant.id) == audit.Verification(False, 4, 3)
    finally:
        await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable"))
        await session.commit()


# --- what gets recorded, and who may read it ---------------------------------


async def test_admin_actions_and_logins_are_audited(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await create_user(session, with_password=True)
    headers = auth_headers(admin)

    await client.post("/auth/login", json={"email": admin.email, "password": PASSWORD})
    key = (await client.post("/api-keys", json={"name": "k"}, headers=headers)).json()
    await client.put("/policy", json={"enabled_types": ["IBAN"]}, headers=headers)
    await client.delete(f"/api-keys/{key['id']}", headers=headers)
    await client.delete(f"/api-keys/{key['id']}", headers=headers)  # no-op: already revoked

    entries = (await client.get("/audit", headers=headers)).json()
    assert [entry["action"] for entry in entries] == [
        "api_key.revoked",
        "policy.updated",
        "api_key.created",
        "auth.login",
    ]
    assert entries[1]["details"] == {"enabled_types": ["IBAN"]}
    assert entries[2]["details"] == {"api_key_id": key["id"], "prefix": key["prefix"]}
    assert {entry["actor"] for entry in entries} == {f"user:{admin.id}"}
    assert key["key"] not in str(entries)  # the secret itself is never recorded

    assert (await client.get("/audit/verify", headers=headers)).json() == {
        "valid": True,
        "entries": 4,
        "first_invalid_seq": None,
    }


async def test_audit_listing_pages_backwards(client: AsyncClient, session: AsyncSession) -> None:
    admin = await create_user(session)
    tenant = await session.get(Tenant, admin.tenant_id)
    assert tenant is not None
    for index in range(5):
        await _append(session, tenant, "test.event", index=index)
    headers = auth_headers(admin)

    newest = (await client.get("/audit?limit=2", headers=headers)).json()
    older = (await client.get("/audit?limit=2&before_seq=4", headers=headers)).json()

    assert [entry["seq"] for entry in newest] == [5, 4]
    assert [entry["seq"] for entry in older] == [3, 2]
    assert (await client.get("/audit?limit=0", headers=headers)).status_code == 422


async def test_audit_is_tenant_scoped_and_role_protected(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await create_user(session)
    tenant = await session.get(Tenant, owner.tenant_id)
    assert tenant is not None
    await _append(session, tenant, "test.event")
    auditor = await create_user(session, role=Role.AUDITOR, tenant=tenant)
    member = await create_user(session, role=Role.MEMBER, tenant=tenant)
    outsider = await create_user(session)

    assert len((await client.get("/audit", headers=auth_headers(auditor))).json()) == 1
    assert (await client.get("/audit", headers=auth_headers(outsider))).json() == []
    assert (await client.get("/audit", headers=auth_headers(member))).status_code == 403
    assert (await client.get("/audit/verify", headers=auth_headers(member))).status_code == 403
    assert (await client.get("/audit")).status_code == 401
