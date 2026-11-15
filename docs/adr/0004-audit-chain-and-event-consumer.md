# ADR 0004: Hash-chained audit log and an in-process stream consumer

Status: accepted (2026-11-15)

## Audit log

Each tenant has its own chain. An entry stores `prev_hash` and
`hash = SHA-256(prev_hash, tenant, seq, actor, action, details, created_at)`.

- **Append-only in the database.** A trigger rejects every UPDATE and every
  DELETE, except when the tenant itself is being deleted (expired guest
  sandboxes cascade away).
- **Tamper-evident beyond the trigger.** A database superuser can disable a
  trigger. `GET /audit/verify` recomputes the chain and names the first entry
  that was changed, removed or reordered. A test does exactly that.
- **Serialised appends.** Two writers reading the same last entry would both
  produce `seq N+1`. A row lock cannot protect an empty chain, so appends take
  `pg_advisory_xact_lock` on the tenant id. Tested with 12 concurrent writers.
- **Same transaction as the change.** Admin actions (API keys, policy, login)
  write their audit entry before the commit, so neither exists without the
  other.
- `details` holds ids, counts and enum values only. Never prompt text, never
  secrets.

What it does not give: protection against someone who rewrites the whole chain
and recomputes every hash. That needs the latest hash anchored outside the
database (for example published periodically). Out of scope here.

## Gateway events

Gateway calls are audited and counted (`usage_daily`) asynchronously. The
gateway publishes one event to a Redis Stream; a consumer in the same process
writes the audit entry and the usage increment in one transaction.

- **Why a stream, if it is the same process?** Durability. Render's free tier
  has no worker and puts the container to sleep. An event that was published
  survives that and is processed after the restart.
- **Why no blocking read?** Cost. Polling every 5 seconds is about 518,000
  commands a month, more than the free Redis tier allows, for an idle app. The
  publisher sets an in-process `asyncio.Event` instead. A 5-minute timeout is
  the safety net.
- **Cost per event: three commands** (XADD, XREADGROUP, XACK), asserted by a
  test. Unacknowledged events are re-read once at startup, not on every wake.
- **At-least-once made idempotent.** The stream id is stored in a unique
  column of the audit entry. A redelivered event finds it and is skipped, and
  because usage is incremented in the same transaction it is never counted
  twice.
- **Poison messages** are logged and acknowledged. Losing one metadata event
  is better than blocking the stream.

Trade-off: this design is exact for one container. With several API replicas
each would need its own consumer name and a blocking read (or a real worker).

## Measured

Startup peak is about 320 MiB (spaCy loads its full vocabulary before it is
pruned), settling at about 180 MiB idle and 195 MiB under use.
