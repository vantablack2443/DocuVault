import time

from redis.asyncio import Redis

from app.core.errors import RateLimitedError

# Runs atomically inside Redis, so two concurrent requests cannot both spend
# the last token. One round trip per request (EVALSHA).
#
# KEYS[1] bucket key
# ARGV    capacity, refill per second, now (seconds), cost
# Returns {allowed (0/1), seconds until enough tokens are back}
_SCRIPT = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local state = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = tonumber(state[1])
local at = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  at = now
end

tokens = math.min(capacity, tokens + math.max(0, now - at) * refill)

local allowed = 0
local retry_after = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after = math.ceil((cost - tokens) / refill)
end

redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'at', tostring(now))
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill) + 1)
return {allowed, retry_after}
"""


class TokenBucket:
    """Allows bursts up to `capacity`, then a steady `refill_per_second`."""

    def __init__(self, redis: Redis, *, capacity: int, refill_per_second: float) -> None:
        self._script = redis.register_script(_SCRIPT)
        self._capacity = capacity
        self._refill = refill_per_second

    async def take(self, key: str, *, cost: int = 1, now: float | None = None) -> None:
        """Spend `cost` tokens or raise `RateLimitedError` with the wait time."""
        allowed, retry_after = await self._script(
            keys=[key],
            args=[self._capacity, self._refill, time.time() if now is None else now, cost],
        )
        if not allowed:
            raise RateLimitedError(retry_after_seconds=max(int(retry_after), 1))
