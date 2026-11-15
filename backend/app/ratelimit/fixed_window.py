from redis.asyncio import Redis

from app.core.errors import RateLimitedError


async def enforce_fixed_window(redis: Redis, key: str, *, limit: int, window_seconds: int) -> None:
    """Coarse counter for low-traffic endpoints. Two Redis commands per call.

    The gateway hot path gets a token bucket (Lua, atomic) in Phase 3.
    """
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if count > limit:
        remaining = await redis.ttl(key)
        raise RateLimitedError(retry_after_seconds=max(int(remaining), 1))
