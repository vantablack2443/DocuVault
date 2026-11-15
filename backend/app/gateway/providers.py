"""Upstream LLM providers. Everything here only ever sees redacted text."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.redaction.engine import PLACEHOLDER

Message = dict[str, str]


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ProviderError(Exception):
    """The provider could not answer (rate limit, outage, timeout, bad payload)."""


class ChatProvider(Protocol):
    name: str

    async def complete(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> Completion: ...

    def stream(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> AsyncIterator[str]: ...


class OpenAICompatibleProvider:
    """Gemini and Groq both expose the OpenAI chat-completions wire format."""

    def __init__(
        self, name: str, *, base_url: str, api_key: str, model: str, client: httpx.AsyncClient
    ) -> None:
        self.name = name
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._client = client

    def _payload(
        self,
        messages: list[Message],
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"model": self._model, "messages": messages, "stream": stream}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    async def complete(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> Completion:
        try:
            response = await self._client.post(
                self._url,
                headers=self._headers,
                json=self._payload(messages, temperature, max_tokens, stream=False),
            )
            if response.status_code != httpx.codes.OK:
                raise ProviderError(f"{self.name} answered {response.status_code}")
            body = response.json()
            usage = body.get("usage") or {}
            return Completion(
                text=body["choices"][0]["message"]["content"] or "",
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError(f"{self.name}: {type(error).__name__}") from error

    async def stream(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers=self._headers,
                json=self._payload(messages, temperature, max_tokens, stream=True),
            ) as response:
                if response.status_code != httpx.codes.OK:
                    raise ProviderError(f"{self.name} answered {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    choices = json.loads(data).get("choices") or [{}]
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError(f"{self.name}: {type(error).__name__}") from error


class ReplayProvider:
    """Deterministic stand-in: no network, no quota, same answer every time.

    Used for tests, for local runs without API keys, and as the last link of
    the failover chain so the public demo keeps working when free tiers are
    exhausted. The answer names the placeholders it received, which makes the
    redact / re-hydrate round trip visible.
    """

    name = "replay"
    _piece = 7  # deliberately small: stream chunks cut placeholders in half

    def _reply(self, messages: list[Message]) -> str:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        placeholders = list(
            dict.fromkeys(match.group() for match in PLACEHOLDER.finditer(last_user))
        )
        if not placeholders:
            return (
                "Replay-Modus (kein LLM-Aufruf): In Ihrer Nachricht wurden keine "
                "personenbezogenen Angaben erkannt."
            )
        return (
            "Replay-Modus (kein LLM-Aufruf): Das Modell hätte nur Platzhalter gesehen, "
            f"nicht die Originalwerte. Erkannt wurden: {', '.join(placeholders[:8])}."
        )

    async def complete(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> Completion:
        return Completion(text=self._reply(messages))

    async def stream(
        self, messages: list[Message], *, temperature: float | None, max_tokens: int | None
    ) -> AsyncIterator[str]:
        reply = self._reply(messages)
        for start in range(0, len(reply), self._piece):
            yield reply[start : start + self._piece]
