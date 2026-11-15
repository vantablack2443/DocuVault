"""Daily cap on live LLM calls made by guest sandboxes.

The public demo needs no sign-up, so anyone can spend the free-tier quota of
the upstream providers. Once the shared daily allowance is used up, guests are
answered by the replay provider: the demo keeps working (redaction, audit log
and dashboard are unaffected), it just stops calling a real model until the
next UTC day. Registered tenants are not affected.
"""

from datetime import UTC, datetime

from redis.asyncio import Redis

KEY_PREFIX = "budget:guest-llm"
_TWO_DAYS = 2 * 24 * 3600


def key_for(day: datetime) -> str:
    return f"{KEY_PREFIX}:{day.date().isoformat()}"


async def spend(redis: Redis, limit: int) -> bool:
    """Count one live call. False when today's allowance is already used up.

    One Redis command per call (two for the first call of a day).
    """
    key = key_for(datetime.now(UTC))
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, _TWO_DAYS)
    return int(count) <= limit
