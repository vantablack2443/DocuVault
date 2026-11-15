"""Gateway building blocks in isolation: re-hydrator, token bucket, providers, failover."""

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.errors import RateLimitedError
from app.gateway import cache
from app.gateway.failover import AllProvidersFailedError, CircuitBreaker, ProviderChain, build_chain
from app.gateway.providers import (
    Completion,
    Message,
    OpenAICompatibleProvider,
    ProviderError,
    ReplayProvider,
)
from app.gateway.rehydrator import MAX_PLACEHOLDER_LENGTH, StreamRehydrator
from app.ratelimit.token_bucket import TokenBucket
from app.redaction.engine import PlaceholderVault
from app.redaction.types import PiiType
from tests.fakes import FakeProvider

MESSAGES: list[Message] = [{"role": "user", "content": "Hallo <PERSON_1>"}]


# --- streaming re-hydrator ---------------------------------------------------


def _vault() -> PlaceholderVault:
    vault = PlaceholderVault()
    vault.placeholder_for(PiiType.PERSON, "Anna Schmidt")
    vault.placeholder_for(PiiType.DATE_OF_BIRTH, "12.03.1985")
    vault.placeholder_for(PiiType.IBAN, "DE89370400440532013000")
    return vault


def _stream(vault: PlaceholderVault, pieces: list[str]) -> str:
    rehydrator = StreamRehydrator(vault)
    return "".join(rehydrator.feed(piece) for piece in pieces) + rehydrator.flush()


TRICKY = (
    "Frau <PERSON_1> (geb. <DATE_OF_BIRTH_1>) zahlt von <IBAN_1>. "
    "Unbekannt: <PERSON_9>. Kein Platzhalter: a < b, <b>, <PERSON_, 3<4 und am Ende <"
)


def test_rehydration_is_identical_for_every_split_position() -> None:
    vault = _vault()
    expected = vault.rehydrate(TRICKY)
    assert "Anna Schmidt" in expected and "<PERSON_1>" not in expected

    for first in range(len(TRICKY) + 1):
        assert _stream(vault, [TRICKY[:first], TRICKY[first:]]) == expected
        for second in range(first, len(TRICKY) + 1, 7):
            pieces = [TRICKY[:first], TRICKY[first:second], TRICKY[second:]]
            assert _stream(vault, pieces) == expected


def test_rehydration_survives_one_character_chunks() -> None:
    vault = _vault()
    assert _stream(vault, list(TRICKY)) == vault.rehydrate(TRICKY)


def test_partial_placeholder_is_never_released_early() -> None:
    rehydrator = StreamRehydrator(_vault())
    assert rehydrator.feed("Hallo <PERS") == "Hallo "
    assert rehydrator.feed("ON_") == ""
    assert rehydrator.feed("1>, wie geht es?") == "Anna Schmidt, wie geht es?"
    assert rehydrator.flush() == ""


def test_text_that_only_looks_like_a_placeholder_is_not_held_forever() -> None:
    rehydrator = StreamRehydrator(_vault())
    shout = "<" + "A" * (MAX_PLACEHOLDER_LENGTH + 5)
    assert rehydrator.feed(shout) == shout


# --- token bucket ------------------------------------------------------------


async def test_token_bucket_allows_burst_then_refills(app: FastAPI) -> None:
    bucket = TokenBucket(app.state.redis, capacity=3, refill_per_second=0.5)
    key = f"test:bucket:{uuid.uuid4()}"

    for _ in range(3):
        await bucket.take(key, now=1000.0)
    with pytest.raises(RateLimitedError) as blocked:
        await bucket.take(key, now=1000.0)
    assert blocked.value.retry_after_seconds == 2  # one token at 0.5 per second

    await bucket.take(key, now=1002.0)  # exactly one token is back
    with pytest.raises(RateLimitedError):
        await bucket.take(key, now=1002.0)

    await bucket.take(key, cost=3, now=2000.0)  # refill is capped at capacity
    with pytest.raises(RateLimitedError):
        await bucket.take(key, now=2000.0)


async def test_token_buckets_are_independent_per_key(app: FastAPI) -> None:
    bucket = TokenBucket(app.state.redis, capacity=1, refill_per_second=0.1)
    first, second = (f"test:bucket:{uuid.uuid4()}" for _ in range(2))
    await bucket.take(first)
    await bucket.take(second)
    with pytest.raises(RateLimitedError):
        await bucket.take(first)


# --- OpenAI-compatible provider (offline, mocked transport) -------------------


def _provider(handler: httpx.MockTransport) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        "gemini",
        base_url="https://llm.invalid/v1/",
        api_key="test-key",
        model="some-model",
        client=httpx.AsyncClient(transport=handler),
    )


async def test_provider_sends_openai_request_and_parses_answer() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            url=str(request.url),
            auth=request.headers["authorization"],
            body=json.loads(request.content),
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Hallo <PERSON_1>"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    completion = await _provider(httpx.MockTransport(handler)).complete(
        MESSAGES, temperature=0.2, max_tokens=64
    )

    assert completion == Completion("Hallo <PERSON_1>", 7, 3)
    assert seen == {
        "url": "https://llm.invalid/v1/chat/completions",
        "auth": "Bearer test-key",
        "body": {
            "model": "some-model",
            "messages": MESSAGES,
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 64,
        },
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": "quota"}),
        httpx.Response(500),
        httpx.Response(200, json={"unexpected": "shape"}),
        httpx.Response(200, text="not json"),
    ],
)
async def test_provider_turns_bad_answers_into_provider_error(response: httpx.Response) -> None:
    provider = _provider(httpx.MockTransport(lambda _: response))
    with pytest.raises(ProviderError):
        await provider.complete(MESSAGES, temperature=None, max_tokens=None)
    if response.status_code != 200:
        with pytest.raises(ProviderError):
            async for _ in provider.stream(MESSAGES, temperature=None, max_tokens=None):
                pass


async def test_provider_turns_network_failure_into_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    with pytest.raises(ProviderError, match="ConnectTimeout"):
        await _provider(httpx.MockTransport(handler)).complete(
            MESSAGES, temperature=None, max_tokens=None
        )


async def test_provider_parses_server_sent_events() -> None:
    lines = [
        ": keep-alive",
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"choices":[{"delta":{"content":"Hallo <PERS"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"ON_1>"}}]}',
        'data: {"choices":[]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"after done"}}]}',
    ]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="\n".join(lines)))

    chunks = [
        chunk
        async for chunk in _provider(transport).stream(MESSAGES, temperature=None, max_tokens=None)
    ]

    assert chunks == ["Hallo <PERS", "ON_1>"]


async def test_replay_provider_is_deterministic_and_names_placeholders() -> None:
    replay = ReplayProvider()
    answer = await replay.complete(MESSAGES, temperature=None, max_tokens=None)
    streamed = [c async for c in replay.stream(MESSAGES, temperature=None, max_tokens=None)]

    assert "<PERSON_1>" in answer.text
    assert "".join(streamed) == answer.text
    assert any("<" in chunk and ">" not in chunk for chunk in streamed)  # placeholder is split
    nothing = await replay.complete(
        [{"role": "user", "content": "Hallo"}], temperature=None, max_tokens=None
    )
    assert "keine" in nothing.text


# --- circuit breaker and failover --------------------------------------------


def test_circuit_breaker_opens_after_threshold_and_half_opens_after_cooldown() -> None:
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=30, clock=lambda: now[0])

    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert not breaker.allow()

    now[0] = 31.0
    assert breaker.allow()  # half-open: one attempt
    breaker.record_failure()
    assert not breaker.allow()  # still failing: open again

    now[0] = 62.0
    breaker.record_success()
    breaker.record_failure()
    assert breaker.allow()  # counter was reset by the success


def _chain(*providers: FakeProvider, threshold: int = 3) -> ProviderChain:
    return ProviderChain(
        providers, lambda: CircuitBreaker(failure_threshold=threshold, cooldown_seconds=60)
    )


async def test_chain_fails_over_and_then_skips_the_broken_provider() -> None:
    broken, healthy = FakeProvider("gemini", fail=True), FakeProvider("groq", reply="ok")
    chain = _chain(broken, healthy, threshold=2)

    for _ in range(3):
        name, completion = await chain.complete(MESSAGES, temperature=None, max_tokens=None)
        assert (name, completion.text) == ("groq", "ok")

    assert broken.calls == 2  # third request did not even try: breaker is open
    assert healthy.calls == 3


async def test_chain_stream_fails_over_before_the_first_chunk() -> None:
    chain = _chain(FakeProvider("gemini", fail=True), FakeProvider("groq", chunks=["a", "b"]))
    name, deltas = await chain.stream(MESSAGES, temperature=None, max_tokens=None)
    assert (name, [delta async for delta in deltas]) == ("groq", ["a", "b"])


async def test_chain_stream_handles_empty_answer() -> None:
    name, deltas = await _chain(FakeProvider("groq", chunks=[])).stream(
        MESSAGES, temperature=None, max_tokens=None
    )
    assert (name, [delta async for delta in deltas]) == ("groq", [])


async def test_chain_raises_when_every_provider_fails() -> None:
    chain = _chain(FakeProvider("gemini", fail=True), FakeProvider("groq", fail=True))
    with pytest.raises(AllProvidersFailedError):
        await chain.complete(MESSAGES, temperature=None, max_tokens=None)
    with pytest.raises(AllProvidersFailedError):
        await chain.stream(MESSAGES, temperature=None, max_tokens=None)


def _names(settings: Settings) -> list[str]:
    chain = build_chain(settings, httpx.AsyncClient())
    return [provider.name for provider, _ in chain._entries]


def test_chain_order_depends_on_mode_and_keys() -> None:
    keys = {"gemini_api_key": "g", "groq_api_key": "q"}
    assert _names(Settings(llm_mode="replay", **keys)) == ["replay"]  # type: ignore[arg-type]
    assert _names(Settings(llm_mode="live", **keys)) == ["gemini", "groq", "replay"]  # type: ignore[arg-type]
    assert _names(Settings(llm_mode="live", groq_api_key="q")) == ["groq", "replay"]
    assert _names(Settings(llm_mode="live")) == ["replay"]


# --- cache key ---------------------------------------------------------------


def test_cache_key_separates_tenants_and_parameters() -> None:
    tenant = uuid.uuid4()
    base = cache.cache_key(tenant, "m", MESSAGES, None, None)
    assert base == cache.cache_key(tenant, "m", list(MESSAGES), None, None)
    assert base.startswith("cache:chat:") and "PERSON" not in base
    others = {
        cache.cache_key(uuid.uuid4(), "m", MESSAGES, None, None),
        cache.cache_key(tenant, "m2", MESSAGES, None, None),
        cache.cache_key(tenant, "m", MESSAGES, 0.5, None),
        cache.cache_key(tenant, "m", MESSAGES, None, 10),
        cache.cache_key(tenant, "m", [{"role": "user", "content": "Hallo <PERSON_2>"}], None, None),
    }
    assert base not in others and len(others) == 5


async def _collect(iterator: AsyncIterator[str]) -> list[str]:
    return [item async for item in iterator]
