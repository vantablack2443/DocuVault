import time
from collections.abc import AsyncIterator, Callable, Sequence

import httpx
import structlog

from app.core.config import Settings
from app.gateway.providers import (
    ChatProvider,
    Completion,
    Message,
    OpenAICompatibleProvider,
    ProviderError,
    ReplayProvider,
)

log = structlog.get_logger()


class AllProvidersFailedError(Exception):
    """Every provider in the chain failed or is switched off by its breaker."""


class CircuitBreaker:
    """Stop calling a provider that keeps failing; try again after a cooldown.

    Free tiers answer 429 for a while once the quota is gone. Skipping the
    provider saves a round trip (and latency) on every request in that window.
    State is per process, which is exact for the single-container deployment.
    """

    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._open_until = 0.0

    def allow(self) -> bool:
        return self._clock() >= self._open_until

    def record_success(self) -> None:
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            # After the cooldown one request is let through (half-open). If it
            # fails, the counter is still at the threshold and the breaker reopens.
            self._open_until = self._clock() + self._cooldown


async def _prepend(first: str | None, rest: AsyncIterator[str]) -> AsyncIterator[str]:
    if first is not None:
        yield first
    async for item in rest:
        yield item


class ProviderChain:
    """Try providers in order; the first one that answers wins."""

    def __init__(
        self, providers: Sequence[ChatProvider], breaker_factory: Callable[[], CircuitBreaker]
    ) -> None:
        self._entries = [(provider, breaker_factory()) for provider in providers]

    async def complete(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> tuple[str, Completion]:
        for provider, breaker in self._entries:
            if not breaker.allow():
                continue
            try:
                completion = await provider.complete(
                    messages, temperature=temperature, max_tokens=max_tokens
                )
            except ProviderError as error:
                breaker.record_failure()
                log.warning("provider_failed", provider=provider.name, error=str(error))
                continue
            breaker.record_success()
            return provider.name, completion
        raise AllProvidersFailedError

    async def stream(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> tuple[str, AsyncIterator[str]]:
        """Fail over only until the first chunk arrives.

        Once text has been sent to the client there is no taking it back, so a
        later error ends that stream instead of restarting on another provider.
        """
        for provider, breaker in self._entries:
            if not breaker.allow():
                continue
            iterator = provider.stream(
                messages, temperature=temperature, max_tokens=max_tokens
            ).__aiter__()
            try:
                first = await anext(iterator, None)
            except ProviderError as error:
                breaker.record_failure()
                log.warning("provider_failed", provider=provider.name, error=str(error))
                continue
            breaker.record_success()
            return provider.name, _prepend(first, iterator)
        raise AllProvidersFailedError


def build_chain(settings: Settings, client: httpx.AsyncClient) -> ProviderChain:
    """Gemini, then Groq, then replay. In replay mode (or without keys): replay only."""
    providers: list[ChatProvider] = []
    if settings.llm_mode == "live":
        if settings.gemini_api_key is not None:
            providers.append(
                OpenAICompatibleProvider(
                    "gemini",
                    base_url=settings.gemini_base_url,
                    api_key=settings.gemini_api_key.get_secret_value(),
                    model=settings.gemini_model,
                    client=client,
                )
            )
        if settings.groq_api_key is not None:
            providers.append(
                OpenAICompatibleProvider(
                    "groq",
                    base_url=settings.groq_base_url,
                    api_key=settings.groq_api_key.get_secret_value(),
                    model=settings.groq_model,
                    client=client,
                )
            )
    providers.append(ReplayProvider())
    return ProviderChain(
        providers,
        lambda: CircuitBreaker(
            failure_threshold=settings.breaker_failure_threshold,
            cooldown_seconds=settings.breaker_cooldown_seconds,
        ),
    )
