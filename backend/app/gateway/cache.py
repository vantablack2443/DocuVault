"""Response cache. Keys and values contain redacted text only, never raw PII."""

import hashlib
import json
import uuid

from redis.asyncio import Redis

from app.gateway.providers import Completion, Message


def cache_key(
    tenant_id: uuid.UUID,
    model: str,
    messages: list[Message],
    temperature: float | None,
    max_tokens: int | None,
) -> str:
    canonical = json.dumps(
        [str(tenant_id), model, messages, temperature, max_tokens],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "cache:chat:" + hashlib.sha256(canonical.encode()).hexdigest()


async def get(redis: Redis, key: str) -> tuple[str, Completion] | None:
    raw = await redis.get(key)
    if raw is None:
        return None
    stored = json.loads(raw)
    return stored["provider"], Completion(
        stored["text"], stored["prompt_tokens"], stored["completion_tokens"]
    )


async def put(redis: Redis, key: str, provider: str, completion: Completion, ttl: int) -> None:
    value = json.dumps(
        {
            "provider": provider,
            "text": completion.text,
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens,
        },
        ensure_ascii=False,
    )
    await redis.set(key, value, ex=ttl)
