"""Background processing of gateway events through a Redis Stream.

Why a stream when the consumer runs in the same process (the free tier has no
separate worker)? Durability: an event written with XADD survives a restart or
a sleeping container and is picked up afterwards. Why no blocking read? Cost:
polling an idle stream every few seconds would use more Redis commands per
month than the free tier allows. The publisher wakes the consumer through an
in-process signal instead, so an idle gateway issues almost no commands
(ADR 0004).
"""

import asyncio
import contextlib
import json
import uuid
from datetime import date
from typing import cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit import service as audit
from app.db import repositories as repo

log = structlog.get_logger()

STREAM = "events:gateway"
GROUP = "workers"
CONSUMER = "api"
MAX_STREAM_LENGTH = 10_000
BATCH = 100


class EventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self.wake = asyncio.Event()

    async def publish(self, fields: dict[str, str]) -> None:
        await self._redis.xadd(STREAM, fields, maxlen=MAX_STREAM_LENGTH, approximate=True)  # type: ignore[arg-type]
        self.wake.set()


class Consumer:
    """At-least-once delivery, made effectively-once by recording the stream id."""

    def __init__(
        self,
        redis: Redis,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        *,
        idle_seconds: float,
    ) -> None:
        self._redis = redis
        self._session_factory = session_factory
        self._bus = bus
        self._idle_seconds = idle_seconds
        # One drain at a time: a second caller waits and then sees the stream empty.
        self._draining = asyncio.Lock()

    async def run(self) -> None:
        """Recover once at start (events left over from before a restart), then on demand."""
        recover = True
        self._bus.wake.set()
        while True:
            # The timeout is only a safety net; normally the publisher wakes us.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._bus.wake.wait(), self._idle_seconds)
            self._bus.wake.clear()
            try:
                await self.drain(recover=recover)
                recover = False
            except Exception:
                log.exception("consumer_drain_failed")

    async def _read(self, start: str) -> list[tuple[str, dict[str, str]]]:
        try:
            response = await self._redis.xreadgroup(GROUP, CONSUMER, {STREAM: start}, count=BATCH)
        except ResponseError as error:
            if "NOGROUP" not in str(error):
                raise
            await self._redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
            response = await self._redis.xreadgroup(GROUP, CONSUMER, {STREAM: start}, count=BATCH)
        # redis-py types this reply very loosely; with decode_responses=True it is
        # [[stream, [(id, {field: value}), ...]]].
        reply = cast(list[tuple[str, list[tuple[str, dict[str, str]]]]], response)
        return reply[0][1] if reply else []

    async def drain(self, *, recover: bool = False) -> int:
        """Process everything available and return how many events were handled.

        ">" reads new events. With `recover`, "0" first re-reads events that were
        delivered but never acknowledged, i.e. a crash happened mid-way.

        Redis commands per wake-up: one XREADGROUP and one XACK per batch. A short
        batch means the stream is empty, so no extra read is spent to confirm it.
        """
        async with self._draining:
            return await self._drain(recover)

    async def _drain(self, recover: bool) -> int:
        processed = 0
        for start in ("0", ">") if recover else (">",):
            while messages := await self._read(start):
                for message_id, fields in messages:
                    try:
                        await self._handle(message_id, fields)
                    except Exception:
                        # A message that can never be processed must not block the
                        # stream. It is metadata; losing one is the lesser evil.
                        log.exception("event_dropped", message_id=message_id)
                await self._redis.xack(STREAM, GROUP, *(message_id for message_id, _ in messages))
                processed += len(messages)
                if len(messages) < BATCH:
                    break
        return processed

    async def _handle(self, message_id: str, fields: dict[str, str]) -> None:
        tenant_id = uuid.UUID(fields["tenant_id"])
        redactions: dict[str, int] = json.loads(fields["redactions"])
        async with self._session_factory() as session:
            if not await repo.tenant_exists(session, tenant_id):
                return  # an expired sandbox; nothing left to account for
            entry = await audit.append(
                session,
                tenant_id,
                actor=fields["actor"],
                action="gateway.request",
                details={
                    "provider": fields["provider"],
                    "status": fields["status"],
                    "cache_hit": fields["cache_hit"] == "1",
                    "stream": fields["stream"] == "1",
                    "redactions": redactions,
                },
                source_id=message_id,
            )
            if entry is None:
                return  # redelivery: audit entry and usage were already written
            # Same transaction as the audit entry, so usage is never counted twice.
            await repo.add_usage(
                session,
                tenant_id,
                date.fromisoformat(fields["day"]),
                requests=1,
                cache_hits=int(fields["cache_hit"]),
                redactions=sum(redactions.values()),
                prompt_tokens=int(fields["prompt_tokens"]),
                completion_tokens=int(fields["completion_tokens"]),
            )
            await session.commit()
