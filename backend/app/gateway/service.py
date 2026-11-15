"""Gateway pipeline: rate limit -> redact -> cache -> provider chain -> re-hydrate -> log."""

import asyncio
import json
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.service import ApiKeyPrincipal
from app.core.config import Settings
from app.db import repositories as repo
from app.db.models import RequestLog
from app.gateway import cache
from app.gateway.failover import ProviderChain
from app.gateway.providers import Completion, Message, ProviderError, ReplayProvider
from app.gateway.rehydrator import StreamRehydrator
from app.gateway.schemas import ChatCompletionRequest
from app.ratelimit.token_bucket import TokenBucket
from app.redaction.engine import PlaceholderVault, RedactionEngine
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
class GatewayDeps:
    engine: RedactionEngine
    chain: ProviderChain
    redis: Redis
    bucket: TokenBucket
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
) -> tuple[list[Message], PlaceholderVault, dict[str, int]]:
    """One vault for the whole conversation: a value keeps its placeholder across turns.

    The vault lives only for this request. Clients resend the full history on
    every call, so redacting it again reproduces the same placeholders and
    nothing has to be stored between requests (ADR 0003).
    """
    vault = PlaceholderVault()
    counts: Counter[str] = Counter()
    messages: list[Message] = []
    for message in body.messages:
        result = engine.redact(message.content, vault=vault, enabled=enabled)
        counts.update(entity.type.value for entity in result.entities)
        messages.append({"role": message.role, "content": result.text})
    if counts:
        messages.insert(0, {"role": "system", "content": PLACEHOLDER_INSTRUCTION})
    return messages, vault, dict(counts)


async def _prepare(
    deps: GatewayDeps, principal: ApiKeyPrincipal, body: ChatCompletionRequest
) -> Prepared:
    started = time.perf_counter()
    await deps.bucket.take(f"ratelimit:gateway:{principal.api_key_id}")
    async with deps.session_factory() as session:
        stored = await repo.get_enabled_types(session, principal.tenant_id)
    enabled = None if stored is None else [PiiType(name) for name in stored]
    messages, vault, counts = await asyncio.to_thread(_redact_all, deps.engine, body, enabled)
    return Prepared(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        started=started,
        model=body.model,
        stream=body.stream,
        messages=messages,
        vault=vault,
        counts=counts,
        cache_key=cache.cache_key(
            principal.tenant_id, body.model, messages, body.temperature, body.max_tokens
        ),
        temperature=body.temperature,
        max_tokens=body.max_tokens,
    )


async def _record(
    deps: GatewayDeps,
    principal: ApiKeyPrincipal,
    prepared: Prepared,
    *,
    provider: str,
    status: str,
    cache_hit: bool,
    completion: Completion,
) -> None:
    """Persist metadata only. A logging failure must never break the response."""
    latency_ms = int((time.perf_counter() - prepared.started) * 1000)
    try:
        async with deps.session_factory() as session:
            session.add(
                RequestLog(
                    tenant_id=principal.tenant_id,
                    api_key_id=principal.api_key_id,
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
    log.info(
        "gateway_request",
        provider=provider,
        status=status,
        cache_hit=cache_hit,
        stream=prepared.stream,
        latency_ms=latency_ms,
        redactions=prepared.counts,
    )


def _cacheable(provider: str) -> bool:
    # Replay answers are free and deterministic; caching them only spends Redis commands.
    return provider != ReplayProvider.name


async def complete(
    deps: GatewayDeps, principal: ApiKeyPrincipal, body: ChatCompletionRequest
) -> ChatResult:
    prepared = await _prepare(deps, principal, body)
    cached = await cache.get(deps.redis, prepared.cache_key)
    if cached is not None:
        provider, completion = cached
    else:
        provider, completion = await deps.chain.complete(
            prepared.messages, temperature=prepared.temperature, max_tokens=prepared.max_tokens
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
        principal,
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
    principal: ApiKeyPrincipal,
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
        principal,
        prepared,
        provider=provider,
        status=status,
        cache_hit=cached is not None,
        completion=completion,
    )


async def open_stream(
    deps: GatewayDeps, principal: ApiKeyPrincipal, body: ChatCompletionRequest
) -> StreamResult:
    """Resolve provider (with failover) before the response starts, then stream.

    Everything that can still turn into an HTTP error (rate limit, all providers
    down) happens here, before the first byte is sent.
    """
    prepared = await _prepare(deps, principal, body)
    hit = await cache.get(deps.redis, prepared.cache_key)
    if hit is not None:
        provider, cached = hit
        deltas = _replay_cached(cached.text)
    else:
        cached = None
        provider, deltas = await deps.chain.stream(
            prepared.messages, temperature=prepared.temperature, max_tokens=prepared.max_tokens
        )
    return StreamResult(
        prepared=prepared,
        provider=provider,
        cache_hit=cached is not None,
        events=_events(deps, principal, prepared, provider, deltas, cached),
    )
