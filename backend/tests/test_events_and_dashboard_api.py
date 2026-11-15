"""Stream consumer, usage aggregation, sandbox sample data and the playground endpoint."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEntry, Role, UsageDaily, User
from app.jobs.events import GROUP, STREAM, Consumer, EventBus
from tests.factories import auth_headers, create_user
from tests.fakes import FakeProvider
from tests.test_gateway_api import IBAN, PROMPT, URL, chat, gateway_client, use_providers


def event(tenant_id: uuid.UUID, **overrides: str) -> dict[str, str]:
    fields = {
        "tenant_id": str(tenant_id),
        "actor": "key:test",
        "provider": "gemini",
        "status": "ok",
        "cache_hit": "0",
        "stream": "0",
        "redactions": json.dumps({"IBAN": 2, "PERSON": 1}),
        "prompt_tokens": "10",
        "completion_tokens": "4",
        "day": datetime.now(UTC).date().isoformat(),
    }
    return {**fields, **overrides}


async def usage_of(session: AsyncSession, user: User) -> UsageDaily | None:
    # populate_existing: re-read the row even if this session saw it before.
    result = await session.execute(
        select(UsageDaily)
        .where(UsageDaily.tenant_id == user.tenant_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def audit_actions(session: AsyncSession, user: User) -> list[str]:
    result = await session.scalars(
        select(AuditEntry.action)
        .where(AuditEntry.tenant_id == user.tenant_id)
        .order_by(AuditEntry.seq)
    )
    return list(result.all())


# --- consumer ----------------------------------------------------------------


async def test_gateway_call_ends_up_in_audit_log_and_usage(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    use_providers(app, FakeProvider("gemini", reply="ok <PERSON_1>"))
    admin, jwt, key = await gateway_client(client, session)

    await client.post(URL, json=chat(), headers=key)
    await client.post(URL, json=chat(), headers=key)  # cache hit
    await app.state.consumer.drain()

    usage = await usage_of(session, admin)
    assert usage is not None
    assert (usage.requests, usage.cache_hits, usage.redactions) == (2, 1, 6)
    assert (usage.prompt_tokens, usage.completion_tokens) == (22, 10)

    entries = (await client.get("/audit", headers=jwt)).json()
    assert [entry["action"] for entry in entries] == [
        "gateway.request",
        "gateway.request",
        "api_key.created",
    ]
    assert entries[0]["details"] == {
        "provider": "gemini",
        "status": "ok",
        "cache_hit": True,
        "stream": False,
        "redactions": {"PERSON": 1, "IBAN": 1, "EMAIL": 1},
    }
    assert entries[0]["actor"].startswith("key:")
    assert IBAN not in json.dumps(entries)
    assert (await client.get("/audit/verify", headers=jwt)).json()["valid"] is True

    days = (await client.get("/usage", headers=jwt)).json()
    assert [(day["requests"], day["cache_hits"]) for day in days] == [(2, 1)]
    listed = (await client.get("/requests", headers=jwt)).json()
    assert [row["cache_hit"] for row in listed] == [True, False]
    assert listed[0]["redaction_counts"] == {"PERSON": 1, "IBAN": 1, "EMAIL": 1}


async def test_redelivered_event_is_not_counted_twice(app: FastAPI, session: AsyncSession) -> None:
    admin = await create_user(session)
    consumer: Consumer = app.state.consumer
    message_id = f"{uuid.uuid4().int % 10**13}-7"  # unique: the test database persists

    await consumer._handle(message_id, event(admin.tenant_id))
    await consumer._handle(message_id, event(admin.tenant_id))

    usage = await usage_of(session, admin)
    assert usage is not None and usage.requests == 1
    assert await audit_actions(session, admin) == ["gateway.request"]


async def test_events_published_before_a_restart_are_processed_afterwards(
    app: FastAPI, session: AsyncSession
) -> None:
    """The stream is the durable part: a fresh consumer picks up what the old one left."""
    admin = await create_user(session)
    redis = app.state.redis
    await redis.xadd(STREAM, event(admin.tenant_id))  # published, nobody was listening
    await redis.xadd(STREAM, event(admin.tenant_id, cache_hit="1"))

    restarted = Consumer(redis, app.state.session_factory, EventBus(redis), idle_seconds=60)
    assert await restarted.drain() == 2
    assert await restarted.drain() == 0

    usage = await usage_of(session, admin)
    assert usage is not None
    assert (usage.requests, usage.cache_hits) == (2, 1)


async def test_delivered_but_unacknowledged_event_is_retried(
    app: FastAPI, session: AsyncSession
) -> None:
    """Crash between reading and acknowledging: the message stays pending and is redone."""
    admin = await create_user(session)
    redis = app.state.redis
    consumer: Consumer = app.state.consumer
    await consumer.drain()  # creates the consumer group
    await redis.xadd(STREAM, event(admin.tenant_id))
    await redis.xreadgroup(GROUP, "api", {STREAM: ">"}, count=10)  # read, then "crash"
    assert await usage_of(session, admin) is None
    assert await consumer.drain() == 0  # a normal wake-up only looks at new events

    assert await consumer.drain(recover=True) == 1  # what happens once at startup

    usage = await usage_of(session, admin)
    assert usage is not None and usage.requests == 1
    assert (await redis.xpending(STREAM, GROUP))["pending"] == 0


async def test_unprocessable_events_do_not_block_the_stream(
    app: FastAPI, session: AsyncSession
) -> None:
    admin = await create_user(session)
    redis = app.state.redis
    await redis.xadd(STREAM, {"garbage": "1"})
    await redis.xadd(STREAM, event(uuid.uuid4()))  # tenant no longer exists
    await redis.xadd(STREAM, event(admin.tenant_id))

    assert await app.state.consumer.drain() == 3

    usage = await usage_of(session, admin)
    assert usage is not None and usage.requests == 1
    assert (await redis.xpending(STREAM, GROUP))["pending"] == 0


async def test_publishing_wakes_the_background_consumer(
    app: FastAPI, session: AsyncSession
) -> None:
    import asyncio

    admin = await create_user(session)
    await app.state.event_bus.publish(event(admin.tenant_id))

    for _ in range(100):  # the consumer task started by the app does the work
        if await usage_of(session, admin) is not None:
            break
        await asyncio.sleep(0.02)
    usage = await usage_of(session, admin)
    assert usage is not None and usage.requests == 1


# --- guest sandbox sample data -----------------------------------------------


async def test_guest_sandbox_starts_with_sample_history(client: AsyncClient) -> None:
    token = (await client.post("/auth/guest")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    usage = (await client.get("/usage", headers=headers)).json()
    requests = (await client.get("/requests", headers=headers)).json()
    audit = (await client.get("/audit", headers=headers)).json()

    assert len(usage) == 6 and all(day["requests"] > 0 for day in usage)
    assert [day["day"] for day in usage] == sorted(day["day"] for day in usage)
    assert {row["provider"] for row in requests} == {"gemini", "groq", "replay"}
    assert [entry["action"] for entry in audit] == ["sandbox.created"]
    assert audit[0]["details"] == {"sample_data": True}
    assert (await client.get("/audit/verify", headers=headers)).json()["valid"] is True
    assert (await client.get("/usage?days=2", headers=headers)).json() == usage[-1:]


async def test_usage_and_requests_are_role_protected(
    client: AsyncClient, session: AsyncSession
) -> None:
    member = auth_headers(await create_user(session, role=Role.MEMBER))
    for path in ("/usage", "/requests"):
        assert (await client.get(path, headers=member)).status_code == 403
        assert (await client.get(path)).status_code == 401
    admin = auth_headers(await create_user(session))
    assert (await client.get("/usage?days=0", headers=admin)).status_code == 422
    assert (await client.get("/requests?limit=500", headers=admin)).status_code == 422


# --- playground --------------------------------------------------------------


async def test_playground_shows_every_step_of_the_pipeline(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider("gemini", reply="Erstattung auf <IBAN_1> für <PERSON_1>.")
    use_providers(app, upstream)
    member = await create_user(session, role=Role.MEMBER)

    response = await client.post(
        "/playground/chat", json={"message": PROMPT}, headers=auth_headers(member)
    )

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["original"] == PROMPT
    assert body["sent_upstream"] == (
        "Frau <PERSON_1> möchte eine Erstattung auf <IBAN_1>. Antwort an <EMAIL_1>."
    )
    assert body["upstream_answer"] == "Erstattung auf <IBAN_1> für <PERSON_1>."
    assert body["final_answer"] == f"Erstattung auf {IBAN} für Schmidt."
    assert (body["provider"], body["cache_hit"]) == ("gemini", False)
    assert [(e["type"], PROMPT[e["start"] : e["end"]]) for e in body["entities"]] == [
        ("PERSON", "Schmidt"),
        ("IBAN", IBAN),
        ("EMAIL", "a.schmidt@example.de"),
    ]
    assert IBAN not in json.dumps(upstream.seen, ensure_ascii=False)

    await app.state.consumer.drain()
    assert await audit_actions(session, member) == ["gateway.request"]
    actor = await session.scalar(
        select(AuditEntry.actor).where(AuditEntry.tenant_id == member.tenant_id)
    )
    assert actor == f"user:{member.id}"


async def test_playground_requires_login_and_validates(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = auth_headers(await create_user(session))
    assert (await client.post("/playground/chat", json={"message": "hi"})).status_code == 401
    empty = await client.post("/playground/chat", json={"message": ""}, headers=headers)
    huge = await client.post("/playground/chat", json={"message": "x" * 5001}, headers=headers)
    assert (empty.status_code, huge.status_code) == (422, 422)


async def test_browser_origin_of_the_dashboard_is_allowed(client: AsyncClient) -> None:
    allowed = await client.options(
        "/auth/guest",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    refused = await client.options(
        "/auth/guest",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in refused.headers


async def test_one_event_costs_three_redis_commands(app: FastAPI, session: AsyncSession) -> None:
    """XADD + XREADGROUP + XACK. The free Redis tier is billed per command."""
    admin = await create_user(session)
    redis = app.state.redis
    consumer: Consumer = app.state.consumer
    await consumer.drain()

    async def calls() -> int:
        stats = await redis.info("commandstats")
        return sum(
            stats.get(f"cmdstat_{name}", {}).get("calls", 0)
            for name in ("xadd", "xreadgroup", "xack")
        )

    before = await calls()
    await redis.xadd(STREAM, event(admin.tenant_id))
    assert await consumer.drain() == 1
    assert await calls() - before == 3
