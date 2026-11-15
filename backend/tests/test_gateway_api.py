"""End-to-end gateway behaviour through HTTP, with scripted upstream providers."""

import json
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RequestLog, Role, User
from app.gateway.failover import CircuitBreaker, ProviderChain
from app.gateway.service import PLACEHOLDER_INSTRUCTION
from app.ratelimit.token_bucket import TokenBucket
from tests.factories import auth_headers, create_user
from tests.fakes import FakeProvider

IBAN = "DE89370400440532013000"
EMAIL = "a.schmidt@example.de"
PROMPT = f"Frau Schmidt möchte eine Erstattung auf {IBAN}. Antwort an {EMAIL}."
URL = "/v1/chat/completions"


def use_providers(app: FastAPI, *providers: FakeProvider) -> None:
    app.state.provider_chain = ProviderChain(
        providers, lambda: CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    )


async def gateway_client(
    client: AsyncClient, session: AsyncSession
) -> tuple[User, dict[str, str], dict[str, str]]:
    """Return an admin, their JWT headers and API-key headers for the gateway."""
    admin = await create_user(session, role=Role.ADMIN)
    jwt = auth_headers(admin)
    key = (await client.post("/api-keys", json={"name": "gw"}, headers=jwt)).json()["key"]
    return admin, jwt, {"Authorization": f"Bearer {key}"}


def chat(content: str = PROMPT, **extra: Any) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": content}], **extra}


def sse_text(body: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse an SSE body into the concatenated content and the raw events."""
    lines = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    events = [json.loads(line) for line in lines[:-1]]
    content = "".join(
        event["choices"][0]["delta"].get("content", "") for event in events if "choices" in event
    )
    return content, events


# --- the core promise: upstream never sees PII, the client gets it back -------


async def test_upstream_sees_placeholders_and_client_gets_originals(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider(
        "gemini", reply="Erstattung an <PERSON_1> auf <IBAN_1>, Kopie an <EMAIL_1>."
    )
    use_providers(app, upstream)
    _, _, key = await gateway_client(client, session)

    response = await client.post(URL, json=chat(), headers=key)

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {
        "role": "assistant",
        "content": f"Erstattung an Schmidt auf {IBAN}, Kopie an {EMAIL}.",
    }
    assert body["usage"] == {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}
    assert response.headers["X-DocuVault-Provider"] == "gemini"
    assert response.headers["X-DocuVault-Cache"] == "miss"
    assert response.headers["X-DocuVault-Redactions"] == "3"

    sent = upstream.seen[0]
    assert sent[0] == {"role": "system", "content": PLACEHOLDER_INSTRUCTION}
    assert sent[1] == {
        "role": "user",
        "content": "Frau <PERSON_1> möchte eine Erstattung auf <IBAN_1>. Antwort an <EMAIL_1>.",
    }
    for secret in (IBAN, EMAIL, "Schmidt"):
        assert secret not in json.dumps(sent, ensure_ascii=False)


async def test_placeholders_are_consistent_across_conversation_turns(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider("gemini", reply="ok")
    use_providers(app, upstream)
    _, _, key = await gateway_client(client, session)
    history = {
        "messages": [
            {"role": "system", "content": "Du bist ein Assistent der Beispiel AG."},
            {"role": "user", "content": f"Mein Konto ist {IBAN}."},
            {"role": "assistant", "content": f"Danke, {IBAN} ist notiert."},
            {"role": "user", "content": "Und DE89 3704 0044 0532 0130 00 stimmt wirklich?"},
        ]
    }

    await client.post(URL, json=history, headers=key)

    # Compact and grouped spellings of one IBAN share a placeholder in every turn.
    turns = [message["content"] for message in upstream.seen[0]][2:]
    assert len(turns) == 3
    assert all(turn.count("<IBAN_1>") == 1 and "DE89" not in turn for turn in turns)
    assert "<IBAN_2>" not in "".join(turns)


async def test_prompt_without_pii_is_forwarded_untouched(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider("gemini", reply="42")
    use_providers(app, upstream)
    _, _, key = await gateway_client(client, session)

    response = await client.post(URL, json=chat("Was ist sechs mal sieben?"), headers=key)

    assert upstream.seen[0] == [{"role": "user", "content": "Was ist sechs mal sieben?"}]
    assert response.headers["X-DocuVault-Redactions"] == "0"


async def test_policy_limits_what_is_redacted(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider("gemini", reply="ok")
    use_providers(app, upstream)
    _, jwt, key = await gateway_client(client, session)

    default = (await client.get("/policy", headers=jwt)).json()["enabled_types"]
    assert "IBAN" in default and len(default) == 9
    updated = await client.put(
        "/policy", json={"enabled_types": ["EMAIL", "IBAN", "IBAN"]}, headers=jwt
    )
    assert updated.json() == {"enabled_types": ["IBAN", "EMAIL"]}
    assert (await client.get("/policy", headers=jwt)).json() == {"enabled_types": ["IBAN", "EMAIL"]}

    await client.post(URL, json=chat(), headers=key)

    assert upstream.seen[0][1]["content"] == (
        "Frau Schmidt möchte eine Erstattung auf <IBAN_1>. Antwort an <EMAIL_1>."
    )


async def test_policy_endpoints_enforce_roles_and_validate(
    client: AsyncClient, session: AsyncSession
) -> None:
    auditor = auth_headers(await create_user(session, role=Role.AUDITOR))
    member = auth_headers(await create_user(session, role=Role.MEMBER))
    admin = auth_headers(await create_user(session))
    body = {"enabled_types": ["IBAN"]}

    assert (await client.get("/policy", headers=auditor)).status_code == 200
    assert (await client.put("/policy", json=body, headers=auditor)).status_code == 403
    assert (await client.get("/policy", headers=member)).status_code == 403
    assert (await client.get("/policy")).status_code == 401
    invalid = await client.put("/policy", json={"enabled_types": ["PASSPORT"]}, headers=admin)
    assert invalid.status_code == 422


# --- streaming ---------------------------------------------------------------


async def test_stream_rehydrates_placeholders_split_across_chunks(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider(
        "gemini", chunks=["Erstattung an <PER", "SON_1> auf <IB", "AN_", "1>", "."]
    )
    use_providers(app, upstream)
    _, _, key = await gateway_client(client, session)

    response = await client.post(URL, json=chat(stream=True), headers=key)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["X-DocuVault-Provider"] == "gemini"
    content, events = sse_text(response.text)
    assert content == f"Erstattung an Schmidt auf {IBAN}."
    assert events[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert events[-1]["choices"][0]["finish_reason"] == "stop"
    assert events[0]["object"] == "chat.completion.chunk"
    # No event ever carries a piece of a placeholder.
    assert "<" not in content


async def test_stream_in_replay_mode_works_without_any_network(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, _, key = await gateway_client(client, session)

    response = await client.post(URL, json=chat(stream=True), headers=key)

    content, _ = sse_text(response.text)
    assert response.headers["X-DocuVault-Provider"] == "replay"
    assert f"Erkannt wurden: Schmidt, {IBAN}, {EMAIL}." in content


async def test_stream_cut_off_mid_way_ends_cleanly_and_is_not_cached(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider(
        "gemini", chunks=["Hallo <PERSON_1>, <IB", "AN_1>"], fail_after_chunks=1
    )
    use_providers(app, upstream)
    admin, _, key = await gateway_client(client, session)

    first = await client.post(URL, json=chat(stream=True), headers=key)
    second = await client.post(URL, json=chat(stream=True), headers=key)

    content, events = sse_text(first.text)
    assert content == "Hallo Schmidt, <IB"  # the dangling tail is released, never dropped
    assert events[-1] == {
        "error": {"type": "upstream_error", "message": "Upstream stream was cut off"}
    }
    assert second.headers["X-DocuVault-Cache"] == "miss"
    statuses = (
        await session.scalars(
            select(RequestLog.status).where(RequestLog.tenant_id == admin.tenant_id)
        )
    ).all()
    assert statuses == ["upstream_error", "upstream_error"]


# --- cache, failover, rate limit ---------------------------------------------


async def test_second_identical_request_is_served_from_cache(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider("gemini", reply="Hallo <PERSON_1>", chunks=["Hallo <PERS", "ON_1>"])
    use_providers(app, upstream)
    _, _, key = await gateway_client(client, session)

    first = await client.post(URL, json=chat(), headers=key)
    second = await client.post(URL, json=chat(), headers=key)
    streamed = await client.post(URL, json=chat(stream=True), headers=key)
    different = await client.post(URL, json=chat(temperature=0.9), headers=key)

    assert upstream.calls == 2  # first and "different"; the other two were cache hits
    assert [r.headers["X-DocuVault-Cache"] for r in (first, second, streamed, different)] == [
        "miss",
        "hit",
        "hit",
        "miss",
    ]
    assert second.json()["choices"][0]["message"]["content"] == "Hallo Schmidt"
    assert second.json()["usage"]["total_tokens"] == 16
    assert sse_text(streamed.text)[0] == "Hallo Schmidt"
    # What sits in Redis is the redacted answer.
    cached = [await app.state.redis.get(k) async for k in app.state.redis.scan_iter("cache:chat:*")]
    assert cached and all("Schmidt" not in value and "<PERSON_1>" in value for value in cached)


async def test_stream_answer_is_cached_for_later_requests(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider("gemini", chunks=["Hallo ", "<PERSON_1>"])
    use_providers(app, upstream)
    _, _, key = await gateway_client(client, session)

    await client.post(URL, json=chat(stream=True), headers=key)
    again = await client.post(URL, json=chat(), headers=key)

    assert upstream.calls == 1
    assert again.headers["X-DocuVault-Cache"] == "hit"
    assert again.json()["choices"][0]["message"]["content"] == "Hallo Schmidt"


async def test_cache_is_not_shared_between_tenants(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    upstream = FakeProvider("gemini", reply="ok")
    use_providers(app, upstream)
    _, _, first_key = await gateway_client(client, session)
    _, _, second_key = await gateway_client(client, session)

    await client.post(URL, json=chat(), headers=first_key)
    other = await client.post(URL, json=chat(), headers=second_key)

    assert other.headers["X-DocuVault-Cache"] == "miss"
    assert upstream.calls == 2


async def test_failover_primary_then_fallback_then_replay(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    from app.gateway.providers import ReplayProvider

    _, _, key = await gateway_client(client, session)

    use_providers(app, FakeProvider("gemini", fail=True), FakeProvider("groq", reply="von groq"))
    fallback = await client.post(URL, json=chat("Hallo eins"), headers=key)
    assert fallback.headers["X-DocuVault-Provider"] == "groq"
    assert fallback.json()["choices"][0]["message"]["content"] == "von groq"

    app.state.provider_chain = ProviderChain(
        [FakeProvider("gemini", fail=True), FakeProvider("groq", fail=True), ReplayProvider()],
        lambda: CircuitBreaker(failure_threshold=3, cooldown_seconds=60),
    )
    replay = await client.post(URL, json=chat("Hallo zwei"), headers=key)
    streamed = await client.post(URL, json=chat("Hallo drei", stream=True), headers=key)
    assert replay.status_code == 200
    assert replay.headers["X-DocuVault-Provider"] == "replay"
    assert streamed.headers["X-DocuVault-Provider"] == "replay"


async def test_all_providers_down_is_a_502_not_a_broken_stream(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    use_providers(app, FakeProvider("gemini", fail=True))
    _, _, key = await gateway_client(client, session)

    plain = await client.post(URL, json=chat(), headers=key)
    streamed = await client.post(URL, json=chat(stream=True), headers=key)

    assert (plain.status_code, streamed.status_code) == (502, 502)
    assert plain.json() == {"detail": "No upstream provider available"}


async def test_gateway_is_rate_limited_per_api_key(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    app.state.token_bucket = TokenBucket(app.state.redis, capacity=2, refill_per_second=0.01)
    _, _, busy = await gateway_client(client, session)
    _, _, quiet = await gateway_client(client, session)

    statuses = [(await client.post(URL, json=chat(), headers=busy)).status_code for _ in range(3)]
    blocked = await client.post(URL, json=chat(stream=True), headers=busy)

    assert statuses == [200, 200, 429]
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert (await client.post(URL, json=chat(), headers=quiet)).status_code == 200


# --- metadata, validation, authentication ------------------------------------


async def test_request_log_holds_metadata_but_no_content(
    app: FastAPI, client: AsyncClient, session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    use_providers(app, FakeProvider("gemini", reply="Hallo <PERSON_1>"))
    admin, _, key = await gateway_client(client, session)
    capsys.readouterr()

    await client.post(URL, json=chat(), headers=key)

    row = (
        await session.execute(select(RequestLog).where(RequestLog.tenant_id == admin.tenant_id))
    ).scalar_one()
    assert (row.provider, row.status, row.stream, row.cache_hit) == ("gemini", "ok", False, False)
    assert (row.prompt_tokens, row.completion_tokens) == (11, 5)
    assert row.redaction_counts == {"PERSON": 1, "IBAN": 1, "EMAIL": 1}
    assert row.api_key_id is not None and row.latency_ms >= 0
    logs = capsys.readouterr().out
    assert "gateway_request" in logs
    for secret in (IBAN, EMAIL, "Schmidt"):
        assert secret not in logs


@pytest.mark.parametrize(
    "body",
    [
        {"messages": []},
        {"messages": [{"role": "robot", "content": "hi"}]},
        {"messages": [{"role": "user", "content": "x" * 50_001}]},
        {"messages": [{"role": "user", "content": "hi"}], "temperature": 3},
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0},
    ],
)
async def test_invalid_requests_are_rejected(
    client: AsyncClient, session: AsyncSession, body: dict[str, Any]
) -> None:
    _, _, key = await gateway_client(client, session)
    assert (await client.post(URL, json=body, headers=key)).status_code == 422


async def test_unknown_openai_fields_are_ignored(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, _, key = await gateway_client(client, session)
    response = await client.post(URL, json=chat("Hallo", top_p=0.9, user="abc"), headers=key)
    assert response.status_code == 200


async def test_gateway_requires_an_api_key(client: AsyncClient, session: AsyncSession) -> None:
    _, jwt, _ = await gateway_client(client, session)
    assert (await client.post(URL, json=chat())).status_code == 401
    assert (await client.post(URL, json=chat(), headers=jwt)).status_code == 401
