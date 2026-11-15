import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Role, Tenant
from tests.factories import auth_headers, create_tenant, create_user


async def _create_key(
    client: AsyncClient, headers: dict[str, str], name: str = "ci"
) -> dict[str, str]:
    response = await client.post("/api-keys", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# --- RBAC --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "list_status", "create_status"),
    [(Role.ADMIN, 200, 201), (Role.AUDITOR, 200, 403), (Role.MEMBER, 403, 403)],
)
async def test_role_matrix(
    client: AsyncClient, session: AsyncSession, role: Role, list_status: int, create_status: int
) -> None:
    headers = auth_headers(await create_user(session, role=role))

    assert (await client.get("/users", headers=headers)).status_code == list_status
    assert (await client.get("/api-keys", headers=headers)).status_code == list_status
    created = await client.post("/api-keys", json={"name": "k"}, headers=headers)
    assert created.status_code == create_status
    revoked = await client.delete(f"/api-keys/{uuid.uuid4()}", headers=headers)
    assert revoked.status_code == (404 if role is Role.ADMIN else 403)


async def test_admin_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/users")).status_code == 401
    assert (await client.get("/api-keys")).status_code == 401
    assert (await client.post("/api-keys", json={"name": "k"})).status_code == 401
    assert (await client.delete(f"/api-keys/{uuid.uuid4()}")).status_code == 401


# --- tenancy -----------------------------------------------------------------


async def test_users_are_listed_per_tenant(client: AsyncClient, session: AsyncSession) -> None:
    tenant = await create_tenant(session)
    admin = await create_user(session, tenant=tenant)
    colleague = await create_user(session, tenant=tenant, role=Role.MEMBER)
    await create_user(session)  # someone else's tenant

    users = (await client.get("/users", headers=auth_headers(admin))).json()

    assert {user["email"] for user in users} == {admin.email, colleague.email}
    assert "password_hash" not in users[0]


async def test_api_keys_are_invisible_and_untouchable_across_tenants(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = auth_headers(await create_user(session))
    intruder = auth_headers(await create_user(session))
    key = await _create_key(client, owner)

    assert (await client.get("/api-keys", headers=intruder)).json() == []
    assert (await client.delete(f"/api-keys/{key['id']}", headers=intruder)).status_code == 404
    assert (await client.get("/v1/models", headers=_bearer(key["key"]))).status_code == 200


# --- API key lifecycle -------------------------------------------------------


async def test_key_is_shown_once_and_stored_as_digest(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = auth_headers(await create_user(session))
    created = await _create_key(client, headers, name="Produktion")

    assert created["key"].startswith(f"dv_{created['prefix']}_")
    listed = (await client.get("/api-keys", headers=headers)).json()
    assert [key["name"] for key in listed] == ["Produktion"]
    assert "key" not in listed[0]

    stored = (
        await session.execute(select(ApiKey).where(ApiKey.prefix == created["prefix"]))
    ).scalar_one()
    assert created["key"] not in (stored.key_hash, stored.prefix)


async def test_key_authenticates_gateway_until_revoked(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = auth_headers(await create_user(session))
    created = await _create_key(client, headers)
    key_headers = _bearer(created["key"])

    models = await client.get("/v1/models", headers=key_headers)
    assert models.status_code == 200
    assert models.json()["data"][0]["id"] == "docuvault-default"
    listed = (await client.get("/api-keys", headers=headers)).json()
    assert listed[0]["last_used_at"] is not None

    assert (await client.delete(f"/api-keys/{created['id']}", headers=headers)).status_code == 204
    assert (await client.delete(f"/api-keys/{created['id']}", headers=headers)).status_code == 204
    assert (await client.get("/v1/models", headers=key_headers)).status_code == 401


async def test_gateway_rejects_everything_that_is_not_a_live_key(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await create_user(session)
    created = await _create_key(client, auth_headers(user))
    prefix = created["prefix"]

    assert (await client.get("/v1/models")).status_code == 401
    for candidate in (
        "garbage",
        "dv_00000000_unknownprefix",
        f"dv_{prefix}_right-prefix-wrong-secret",
        auth_headers(user)["Authorization"].removeprefix("Bearer "),  # a JWT is not an API key
    ):
        assert (await client.get("/v1/models", headers=_bearer(candidate))).status_code == 401


async def test_key_of_expired_tenant_is_rejected(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await create_user(session)
    created = await _create_key(client, auth_headers(user))
    await session.execute(
        update(Tenant)
        .where(Tenant.id == user.tenant_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()

    assert (await client.get("/v1/models", headers=_bearer(created["key"]))).status_code == 401


async def test_key_name_is_validated(client: AsyncClient, session: AsyncSession) -> None:
    headers = auth_headers(await create_user(session))
    assert (await client.post("/api-keys", json={"name": ""}, headers=headers)).status_code == 422
    too_long = await client.post("/api-keys", json={"name": "x" * 81}, headers=headers)
    assert too_long.status_code == 422
