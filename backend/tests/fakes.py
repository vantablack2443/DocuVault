from collections.abc import AsyncIterator

from app.gateway.providers import Completion, Message, ProviderError


class FakeProvider:
    """Scripted upstream: records what it was sent, answers or fails on demand."""

    def __init__(
        self,
        name: str,
        *,
        reply: str = "",
        chunks: list[str] | None = None,
        fail: bool = False,
        fail_after_chunks: int | None = None,
    ) -> None:
        self.name = name
        self.calls = 0
        self.seen: list[list[Message]] = []
        self._reply = reply
        self._chunks = chunks
        self._fail = fail
        self._fail_after_chunks = fail_after_chunks

    def _enter(self, messages: list[Message]) -> None:
        self.calls += 1
        self.seen.append(messages)
        if self._fail:
            raise ProviderError(f"{self.name} is down")

    async def complete(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> Completion:
        self._enter(messages)
        return Completion(self._reply, prompt_tokens=11, completion_tokens=5)

    async def stream(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> AsyncIterator[str]:
        self._enter(messages)
        chunks = self._chunks if self._chunks is not None else [self._reply]
        for index, chunk in enumerate(chunks):
            if self._fail_after_chunks is not None and index >= self._fail_after_chunks:
                raise ProviderError(f"{self.name} dropped the connection")
            yield chunk
