# ADR 0003: The placeholder vault is per request and never stored

Status: accepted (2026-11-15). Replaces the plan's "mappings live in Redis with
a short TTL, encrypted".

## Context

The vault maps placeholders (`<PERSON_1>`) to original values. The plan stored
it in Redis so that later turns of a conversation could reuse the mapping.

## Decision

The vault exists only in memory for the duration of one request.

The OpenAI chat protocol is stateless: the client sends the whole history with
every call, and the history holds original values (answers are re-hydrated
before they are returned). Redacting that history again with one fresh vault
reproduces consistent placeholders, because placeholder numbers are assigned in
order of first appearance and equal values share a placeholder.

## Why

- No raw PII at rest anywhere: not in Postgres, not in Redis, not in logs.
  There is no key to manage and nothing to leak or to expire.
- No conversation identifier is needed, so any OpenAI client works unchanged.
- Fewer Redis commands on the free tier.

## Consequences

- The response cache stores answers in redacted form (placeholders), keyed by
  a hash of the redacted prompt and scoped to the tenant. A cache hit is
  re-hydrated with the current request's vault.
- Placeholder numbers are stable within a request, not across unrelated
  requests. Nothing depends on that.
- If a model invents a placeholder that was never issued, it is passed through
  unchanged rather than guessed at.
- Streaming failover happens only before the first chunk. After that an
  upstream error ends the stream with an error event; partial answers are not
  cached.

## Related deviations in Phase 3

- `redaction_events` was not built as a table. Per-type counts are a JSONB
  column on `requests`, which is all the dashboard needs.
- Request metadata is written directly after the response. The Redis Stream
  consumer for usage aggregation stays in Phase 4.
- The circuit breaker keeps its state in process memory, which is exact for a
  single container.
