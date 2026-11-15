"""Phase 5 hardening: proxy-aware client address, guest LLM budget, metrics."""

from datetime import UTC, datetime

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.config import Settings, get_settings
from app.core.network import client_ip
from app.db.models import Role
from app.gateway import budget
from tests.factories import auth_headers, create_user
from tests.fakes import FakeProvider
from tests.test_gateway_api import use_providers

# --- client address ----------------------------------------------------------


def request_from(peer: str | None, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    client = (peer, 1234) if peer is not None else None
    return Request({"type": "http", "headers": headers, "client": client})


def test_forwarded_header_is_ignored_without_a_trusted_proxy() -> None:
    assert client_ip(request_from("10.0.0.1", "6.6.6.6"), 0) == "10.0.0.1"
    assert client_ip(request_from(None), 0) == "unknown"


def test_address_is_taken_from_the_entry_the_trusted_proxy_wrote() -> None:
    # The client sent a forged first entry; the proxy appended what it really saw.
    forged = request_from("10.0.0.1", "6.6.6.6, 203.0.113.9")
    assert client_ip(forged, 1) == "203.0.113.9"
    assert client_ip(request_from("10.0.0.1", "203.0.113.9 , 172.16.0.5"), 2) == "203.0.113.9"


def test_falls_back_to_the_socket_when_the_header_is_too_short() -> None:
    assert client_ip(request_from("10.0.0.1", "203.0.113.9"), 2) == "10.0.0.1"
    assert client_ip(request_from("10.0.0.1"), 1) == "10.0.0.1"
    assert client_ip(request_from("10.0.0.1", " , "), 1) == "10.0.0.1"


async def test_guest_limit_counts_per_forwarded_address(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"trusted_proxy_hops": 1, "guest_logins_per_hour": 1}
    )
    first, second = (f"198.51.100.{datetime.now(UTC).microsecond % 250}", "198.51.100.251")
    redis = app.state.redis
    await redis.delete(f"ratelimit:guest-login:{first}", f"ratelimit:guest-login:{second}")

    async def guest(address: str) -> int:
        response = await client.post("/auth/guest", headers={"X-Forwarded-For": address})
        return response.status_code

    assert await guest(first) == 200
    assert await guest(first) == 429
    assert await guest(second) == 200  # another visitor behind the same proxy


# --- guest LLM budget --------------------------------------------------------


async def test_guests_fall_back_to_replay_when_the_daily_budget_is_spent(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    live = FakeProvider("gemini", reply="live answer")
    use_providers(app, live, FakeProvider("replay", reply="recorded answer"))
    app.state.settings = app.state.settings.model_copy(
        update={"llm_mode": "live", "guest_daily_llm_calls": 1}
    )
    await app.state.redis.delete(budget.key_for(datetime.now(UTC)))
    token = (await client.post("/auth/guest")).json()["access_token"]
    guest = {"Authorization": f"Bearer {token}"}

    async def ask(text: str, headers: dict[str, str]) -> tuple[str, str]:
        response = await client.post("/playground/chat", json={"message": text}, headers=headers)
        assert response.status_code == 200
        body = response.json()
        return body["provider"], body["final_answer"]

    assert await ask("erste Frage", guest) == ("gemini", "live answer")
    assert await ask("zweite Frage", guest) == ("replay", "recorded answer")
    assert len(live.seen) == 1
    # Redaction keeps working without a live model, and paying tenants are unaffected.
    member = auth_headers(await create_user(session, role=Role.MEMBER))
    assert await ask("dritte Frage", member) == ("gemini", "live answer")


async def test_budget_is_not_touched_in_replay_mode(app: FastAPI, client: AsyncClient) -> None:
    use_providers(app, FakeProvider("replay", reply="recorded answer"))
    key = budget.key_for(datetime.now(UTC))
    await app.state.redis.delete(key)
    token = (await client.post("/auth/guest")).json()["access_token"]

    response = await client.post(
        "/playground/chat", json={"message": "hallo"}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.json()["provider"] == "replay"
    assert await app.state.redis.exists(key) == 0


async def test_streaming_respects_the_budget(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    use_providers(app, FakeProvider("gemini", reply="live"), FakeProvider("replay", reply="rec"))
    app.state.settings = app.state.settings.model_copy(
        update={"llm_mode": "live", "guest_daily_llm_calls": 0}
    )
    token = (await client.post("/auth/guest")).json()["access_token"]
    created = await client.post(
        "/api-keys", json={"name": "k"}, headers={"Authorization": f"Bearer {token}"}
    )
    key = {"Authorization": f"Bearer {created.json()['key']}"}

    response = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hallo"}], "stream": True},
        headers=key,
    )

    assert response.headers["x-docuvault-provider"] == "replay"


# --- metrics -----------------------------------------------------------------


async def test_metrics_are_off_without_a_token(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"metrics_token": None}
    )
    assert (await client.get("/metrics")).status_code == 404


async def test_metrics_need_the_token_and_use_route_templates(
    app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"metrics_token": SecretStr("scrape-me")}
    )
    use_providers(app, FakeProvider("gemini", reply="ok"))
    admin = auth_headers(await create_user(session))
    await client.get("/healthz")
    await client.get("/no/such/path/12345")
    await client.delete("/api-keys/00000000-0000-0000-0000-000000000000", headers=admin)
    await client.post("/playground/chat", json={"message": "Mail an a.b@example.de"}, headers=admin)

    assert (await client.get("/metrics")).status_code == 401
    wrong = await client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    response = await client.get("/metrics", headers={"Authorization": "Bearer scrape-me"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert 'docuvault_http_requests_total{method="GET",route="/healthz",status="200"}' in text
    assert 'route="unmatched",status="404"' in text
    assert 'route="/api-keys/{key_id}"' in text and "00000000-0000" not in text
    assert 'docuvault_gateway_requests_total{cache="miss",provider="gemini",status="ok"}' in text
    assert 'docuvault_redactions_total{type="EMAIL"}' in text
    assert "docuvault_http_request_duration_seconds_bucket" in text


# --- hosted Postgres connection strings --------------------------------------


def test_libpq_style_url_is_translated_for_asyncpg() -> None:
    hosted = "postgresql://u:p%40ss@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require&channel_binding=require"
    settings = Settings(database_url=hosted)
    assert settings.database_url == (
        "postgresql+asyncpg://u:p%40ss@ep-x.eu-central-1.aws.neon.tech/db?ssl=require"
    )
    plain = "postgresql+asyncpg://docuvault:docuvault@postgres:5432/docuvault"
    assert Settings(database_url=plain).database_url == plain
