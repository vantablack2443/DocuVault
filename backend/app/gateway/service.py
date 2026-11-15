"""Gateway pipeline: rate limit -> redact -> cache -> provider chain -> re-hydrate -> log."""

import asyncio
import json
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import metrics
from app.core.config import Settings
from app.db import repositories as repo
from app.db.models import RequestLog
from app.gateway import budget, cache
from app.gateway.failover import ProviderChain
from app.gateway.providers import Completion, Message, ProviderError, ReplayProvider
from app.gateway.rehydrator import StreamRehydrator
from app.gateway.schemas import ChatCompletionRequest
from app.jobs.events import EventBus
from app.ratelimit.token_bucket import TokenBucket
from app.redaction.engine import Entity, PlaceholderVault, RedactionEngine
from app.redaction.types import PiiType

log = structlog.get_logger()

# Sent to the model whenever something was redacted, so it does not "correct",
# translate or drop the placeholders.
PLACEHOLDER_INSTRUCTION = (
    "Some values in this conversation were replaced by placeholders such as <PERSON_1> or "
    "<IBAN_2> to protect personal data. Treat each placeholder as the value it stands for. "
    "Whenever you refer to such a value, repeat its placeholder exactly, character for "
    "character. Never invent new placeholders."
)
_CACHED_STREAM_PIECE = 24


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is calling: an API key (gateway) or a signed-in user (playground)."""

    tenant_id: uuid.UUID
    api_key_id: uuid.UUID | None
    actor: str
    rate_key: str
    is_guest: bool = False


@dataclass(frozen=True, slots=True)
class GatewayDeps:
    engine: RedactionEngine
    chain: ProviderChain
    redis: Redis
    bucket: TokenBucket
    bus: EventBus
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings


@dataclass(frozen=True, slots=True)
class Prepared:
    """A request after redaction, ready to leave the building."""

    id: str
    created: int
    started: float
    model: str
    stream: bool
    messages: list[Message]
    vault: PlaceholderVault
    counts: dict[str, int]
    # Per message, in request order: where each value was found.
    entities: list[tuple[Entity, ...]]
    cache_key: str
    temperature: float | None
    max_tokens: int | None


@dataclass(frozen=True, slots=True)
class ChatResult:
    prepared: Prepared
    provider: str
    cache_hit: bool
    text: str
    completion: Completion


@dataclass(frozen=True, slots=True)
class StreamResult:
    prepared: Prepared
    provider: str
    cache_hit: bool
    events: AsyncIterator[str]


def _redact_all(
    engine: RedactionEngine, body: ChatCompletionRequest, enabled: list[PiiType] | None
) -> tuple[list[Message], PlaceholderVault, dict[str, int], list[tuple[Entity, ...]]]:
    """One vault for the whole conversation: a value keeps its placeholder across turns.

    The vault lives only for this request. Clients resend the full history on
    every call, so redacting it again reproduces the same placeholders and
    nothing has to be stored between requests (ADR 0003).
    """
    vault = PlaceholderVault()
    counts: Counter[str] = Counter()
    messages: list[Message] = []
    entities: list[tuple[Entity, ...]] = []
    for message in body.messages:
        result = engine.redact(message.content, vault=vault, enabled=enabled)
        counts.update(entity.type.value for entity in result.entities)
        entities.append(result.entities)
        messages.append({"role": message.role, "content": result.text})
    if counts:
        messages.insert(0, {"role": "system", "content": PLACEHOLDER_INSTRUCTION})
    return messages, vault, dict(counts), entities


async def _prepare(deps: GatewayDeps, caller: Caller, body: ChatCompletionRequest) -> Prepared:
    started = time.perf_counter()
    await deps.bucket.take(caller.rate_key)
    async with deps.session_factory() as session:
        stored = await repo.get_enabled_types(session, caller.tenant_id)
    enabled = None if stored is None else [PiiType(name) for name in stored]
    messages, vault, counts, entities = await asyncio.to_thread(
        _redact_all, deps.engine, body, enabled
    )
    return Prepared(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        started=started,
        model=body.model,
        stream=body.stream,
        messages=messages,
        vault=vault,
        counts=counts,
        entities=entities,
        cache_key=cache.cache_key(
            caller.tenant_id, body.model, messages, body.temperature, body.max_tokens
        ),
        temperature=body.temperature,
        max_tokens=body.max_tokens,
    )


async def _record(
    deps: GatewayDeps,
    caller: Caller,
    prepared: Prepared,
    *,
    provider: str,
    status: str,
    cache_hit: bool,
    completion: Completion,
) -> None:
    """Persist metadata only. A logging failure must never break the response."""
    metrics.GATEWAY_REQUESTS.labels(provider, status, "hit" if cache_hit else "miss").inc()
    if not cache_hit:  # a cache hit redacts too, but is counted once per distinct upstream call
        for pii_type, count in prepared.counts.items():
            metrics.REDACTIONS.labels(pii_type).inc(count)
    latency_ms = int((time.perf_counter() - prepared.started) * 1000)
    try:
        async with deps.session_factory() as session:
            session.add(
                RequestLog(
                    tenant_id=caller.tenant_id,
                    api_key_id=caller.api_key_id,
                    model=prepared.model,
                    provider=provider,
                    status=status,
                    stream=prepared.stream,
                    cache_hit=cache_hit,
                    latency_ms=latency_ms,
                    prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                    redaction_counts=prepared.counts,
                )
            )
            await session.commit()
    except Exception:
        log.exception("request_log_failed")
    try:
        # Audit entry and usage counters are written by the stream consumer.
        await deps.bus.publish(
            {
                "tenant_id": str(caller.tenant_id),
                "actor": caller.actor,
                "provider": provider,
                "status": status,
                "cache_hit": "1" if cache_hit else "0",
                "stream": "1" if prepared.stream else "0",
                "redactions": json.dumps(prepared.counts),
                "prompt_tokens": str(completion.prompt_tokens),
                "completion_tokens": str(completion.completion_tokens),
                "day": datetime.now(UTC).date().isoformat(),
            }
        )
    except Exception:
        log.exception("event_publish_failed")
    log.info(
        "gateway_request",
        provider=provider,
        status=status,
        cache_hit=cache_hit,
        stream=prepared.stream,
        latency_ms=latency_ms,
        redactions=prepared.counts,
    )


async def _restriction(deps: GatewayDeps, caller: Caller) -> str | None:
    """Guests share a daily allowance of live LLM calls; beyond it they get replay."""
    if not caller.is_guest or deps.settings.llm_mode != "live":
        return None
    if await budget.spend(deps.redis, deps.settings.guest_daily_llm_calls):
        return None
    metrics.GUEST_BUDGET_EXHAUSTED.inc()
    return ReplayProvider.name


def _cacheable(provider: str) -> bool:
    # Replay answers are free and deterministic; caching them only spends Redis commands.
    return provider != ReplayProvider.name


async def complete(deps: GatewayDeps, caller: Caller, body: ChatCompletionRequest) -> ChatResult:
    prepared = await _prepare(deps, caller, body)
    cached = await cache.get(deps.redis, prepared.cache_key)
    if cached is not None:
        provider, completion = cached
    else:
        provider, completion = await deps.chain.complete(
            prepared.messages,
            temperature=prepared.temperature,
            max_tokens=prepared.max_tokens,
            only=await _restriction(deps, caller),
        )
        if _cacheable(provider):
            await cache.put(
                deps.redis,
                prepared.cache_key,
                provider,
                completion,
                deps.settings.cache_ttl_seconds,
            )
    await _record(
        deps,
        caller,
        prepared,
        provider=provider,
        status="ok",
        cache_hit=cached is not None,
        completion=completion,
    )
    return ChatResult(
        prepared=prepared,
        provider=provider,
        cache_hit=cached is not None,
        text=prepared.vault.rehydrate(completion.text),
        completion=completion,
    )


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chunk(prepared: Prepared, delta: dict[str, str], finish_reason: str | None = None) -> str:
    return _sse(
        {
            "id": prepared.id,
            "object": "chat.completion.chunk",
            "created": prepared.created,
            "model": prepared.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
    )


async def _replay_cached(text: str) -> AsyncIterator[str]:
    for start in range(0, len(text), _CACHED_STREAM_PIECE):
        yield text[start : start + _CACHED_STREAM_PIECE]


async def _events(
    deps: GatewayDeps,
    caller: Caller,
    prepared: Prepared,
    provider: str,
    deltas: AsyncIterator[str],
    cached: Completion | None,
) -> AsyncIterator[str]:
    rehydrator = StreamRehydrator(prepared.vault)
    collected: list[str] = []
    status = "ok"
    yield _chunk(prepared, {"role": "assistant", "content": ""})
    try:
        async for delta in deltas:
            collected.append(delta)
            text = rehydrator.feed(delta)
            if text:
                yield _chunk(prepared, {"content": text})
    except ProviderError as error:
        status = "upstream_error"
        log.warning("provider_failed_mid_stream", provider=provider, error=str(error))
    tail = rehydrator.flush()
    if tail:
        yield _chunk(prepared, {"content": tail})
    if status == "ok":
        yield _chunk(prepared, {}, finish_reason="stop")
    else:
        yield _sse({"error": {"type": "upstream_error", "message": "Upstream stream was cut off"}})
    yield "data: [DONE]\n\n"

    completion = cached or Completion("".join(collected))
    if status == "ok" and cached is None and _cacheable(provider):
        await cache.put(
            deps.redis, prepared.cache_key, provider, completion, deps.settings.cache_ttl_seconds
        )
    await _record(
        deps,
        caller,
        prepared,
        provider=provider,
        status=status,
        cache_hit=cached is not None,
        completion=completion,
    )


async def open_stream(
    deps: GatewayDeps, caller: Caller, body: ChatCompletionRequest
) -> StreamResult:
    """Resolve provider (with failover) before the response starts, then stream.

    Everything that can still turn into an HTTP error (rate limit, all providers
    down) happens here, before the first byte is sent.
    """
    prepared = await _prepare(deps, caller, body)
    hit = await cache.get(deps.redis, prepared.cache_key)
    if hit is not None:
        provider, cached = hit
        deltas = _replay_cached(cached.text)
    else:
        cached = None
        provider, deltas = await deps.chain.stream(
            prepared.messages,
            temperature=prepared.temperature,
            max_tokens=prepared.max_tokens,
            only=await _restriction(deps, caller),
        )
    return StreamResult(
        prepared=prepared,
        provider=provider,
        cache_hit=cached is not None,
        events=_events(deps, caller, prepared, provider, deltas, cached),
    )
