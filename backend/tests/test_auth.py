from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import RefreshToken, Tenant, User
from tests.factories import PASSWORD, auth_headers, create_user


async def _login(client: AsyncClient, user: User, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post("/auth/login", json={"email": user.email, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- login -------------------------------------------------------------------


async def test_login_returns_token_pair(client: AsyncClient, session: AsyncSession) -> None:
    user = await create_user(session, with_password=True)

    body = await _login(client, user)

    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().access_token_ttl_minutes * 60
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json() == {
        "id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "email": user.email,
        "role": "admin",
        "is_guest": False,
    }


async def test_login_email_is_case_insensitive(client: AsyncClient, session: AsyncSession) -> None:
    user = await create_user(session, with_password=True)
    response = await client.post(
        "/auth/login", json={"email": f"  {user.email.upper()} ", "password": PASSWORD}
    )
    assert response.status_code == 200


async def test_login_failures_are_indistinguishable(
    client: AsyncClient, session: AsyncSession
) -> None:
    active = await create_user(session, with_password=True)
    inactive = await create_user(session, with_password=True, is_active=False)
    passwordless = await create_user(session)

    attempts = [
        {"email": active.email, "password": "wrong"},
        {"email": "nobody@example.test", "password": PASSWORD},
        {"email": inactive.email, "password": PASSWORD},
        {"email": passwordless.email, "password": PASSWORD},
    ]
    responses = [await client.post("/auth/login", json=attempt) for attempt in attempts]

    assert {response.status_code for response in responses} == {401}
    assert len({response.text for response in responses}) == 1
    assert responses[0].headers["WWW-Authenticate"] == "Bearer"


async def test_only_token_digest_is_stored(client: AsyncClient, session: AsyncSession) -> None:
    user = await create_user(session, with_password=True)
    body = await _login(client, user)

    stored = (
        await session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
    ).scalar_one()
    assert stored.token_hash != body["refresh_token"]
    assert len(stored.token_hash) == 64


# --- access token ------------------------------------------------------------


async def test_me_requires_a_valid_token(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    garbage = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert garbage.status_code == 401


async def test_deactivated_user_loses_access_immediately(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await create_user(session)
    headers = auth_headers(user)
    assert (await client.get("/auth/me", headers=headers)).status_code == 200

    await session.execute(update(User).where(User.id == user.id).values(is_active=False))
    await session.commit()

    assert (await client.get("/auth/me", headers=headers)).status_code == 401


async def test_token_for_deleted_user_is_rejected(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await create_user(session)
    headers = auth_headers(user)
    await session.delete(user)
    await session.commit()

    assert (await client.get("/auth/me", headers=headers)).status_code == 401


# --- refresh and logout ------------------------------------------------------


async def test_refresh_rotates_the_token(client: AsyncClient, session: AsyncSession) -> None:
    user = await create_user(session, with_password=True)
    first = await _login(client, user)

    rotated = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != first["refresh_token"]


async def test_reusing_a_refresh_token_revokes_the_family(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await create_user(session, with_password=True)
    first = await _login(client, user)
    second = (
        await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).json()

    replay = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401

    # The legitimate holder is signed out too: the token was stolen from someone.
    after = await client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert after.status_code == 401


async def test_refresh_rejects_unknown_and_expired_tokens(
    client: AsyncClient, session: AsyncSession
) -> None:
    unknown = await client.post("/auth/refresh", json={"refresh_token": "never-issued"})
    assert unknown.status_code == 401

    user = await create_user(session, with_password=True)
    body = await _login(client, user)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()

    expired = await client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert expired.status_code == 401


async def test_refresh_rejected_for_deactivated_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await create_user(session, with_password=True)
    body = await _login(client, user)
    await session.execute(update(User).where(User.id == user.id).values(is_active=False))
    await session.commit()

    response = await client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert response.status_code == 401


async def test_logout_revokes_and_is_idempotent(client: AsyncClient, session: AsyncSession) -> None:
    user = await create_user(session, with_password=True)
    body = await _login(client, user)
    payload = {"refresh_token": body["refresh_token"]}

    assert (await client.post("/auth/logout", json=payload)).status_code == 204
    assert (await client.post("/auth/logout", json=payload)).status_code == 204
    assert (await client.post("/auth/refresh", json=payload)).status_code == 401


# --- guest sandbox -----------------------------------------------------------


async def test_guest_login_creates_isolated_seeded_sandbox(client: AsyncClient) -> None:
    first = (await client.post("/auth/guest")).json()
    second = (await client.post("/auth/guest")).json()
    assert first["refresh_token"] is None
    assert first["expires_in"] == get_settings().guest_tenant_ttl_minutes * 60

    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    me = (await client.get("/auth/me", headers=first_headers)).json()
    assert me["is_guest"] is True
    assert me["role"] == "admin"

    users = (await client.get("/users", headers=first_headers)).json()
    assert sorted(user["role"] for user in users) == ["admin", "auditor", "member"]

    other = (
        await client.get("/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"})
    ).json()
    assert other["tenant_id"] != me["tenant_id"]


async def test_guest_login_is_rate_limited_per_ip(client: AsyncClient) -> None:
    limit = get_settings().guest_logins_per_hour
    for _ in range(limit):
        assert (await client.post("/auth/guest")).status_code == 200

    blocked = await client.post("/auth/guest")

    assert blocked.status_code == 429
    assert 0 < int(blocked.headers["Retry-After"]) <= 3600


async def test_expired_sandbox_is_locked_then_deleted(
    client: AsyncClient, session: AsyncSession
) -> None:
    token = (await client.post("/auth/guest")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tenant_id = (await client.get("/auth/me", headers=headers)).json()["tenant_id"]

    await session.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()

    # Token is still cryptographically valid, but the sandbox is over.
    assert (await client.get("/auth/me", headers=headers)).status_code == 401

    # The next guest login sweeps expired sandboxes; users cascade away with them.
    assert (await client.post("/auth/guest")).status_code == 200
    tenants = await session.scalar(select(func.count()).where(Tenant.id == tenant_id))
    users = await session.scalar(select(func.count()).where(User.tenant_id == tenant_id))
    assert (tenants, users) == (0, 0)
