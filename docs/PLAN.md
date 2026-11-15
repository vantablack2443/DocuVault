# DocuVault — German PII-redaction LLM gateway (flagship project plan)

## Context

Werkstudent applications are being rejected for lack of visible backend, database and DevOps evidence. This project closes that gap with one deployable system: an OpenAI-compatible gateway that redacts German PII before prompts reach an external LLM, re-hydrates the response, and records everything in a tamper-evident audit log.

Decisions confirmed by the user:
- Direction: DocuVault, gateway-first. RAG reduced to one small optional feature.
- Stack: Python + FastAPI (target: startups / AI / product companies).
- Hosting: Render free tier with keep-warm; Neon Postgres; Upstash Redis; Cloudflare Pages frontend.
- Budget: ~15 h/week, about 3 weeks (~45 h). Scope below is cut to fit that.

Wording rule for all docs and UI: never claim "DSGVO-compliant" or "sovereign". Claim "PII minimisation before external LLM calls".

## First step after approval

Workspace is an empty session folder. Ask the user for a project folder (`request_directory`), then `git init` and start Phase 0 there.

## Stack

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async, asyncpg), Alembic, redis-py (asyncio), httpx, structlog, prometheus client, `uv` for dependencies.
- Redaction: regex + checksum validators (IBAN mod-97, Steuer-ID check digit, email, phone, postcode) plus spaCy `de_core_news_sm` for names/locations. No transformer models (512 MB limit).
- LLM: Gemini free tier primary, Groq fallback, deterministic replay mode.
- Frontend: Vite + React + TypeScript, static build on Cloudflare Pages.
- Quality: ruff, mypy, pytest, testcontainers (real Postgres + Redis), coverage gate in CI.
- Ops: multi-stage Dockerfile, `docker-compose.yml`, one Helm chart, `kind` smoke test in GitHub Actions.

## Repository structure

```
docuvault/
  CLAUDE.md                 project constitution
  README.md                 Mermaid diagram, demo GIF, benchmark table, coverage badge
  docker-compose.yml        api, postgres (pgvector image), redis, web, seed
  backend/
    Dockerfile              multi-stage, non-root, spaCy model baked in
    pyproject.toml
    alembic/                migrations
    app/
      main.py               app factory, lifespan (starts stream consumer)
      core/                 config, logging, security (JWT, argon2), deps
      db/                   models, session, repositories
      auth/                 login, refresh, guest login, RBAC dependency
      redaction/            detectors/, engine.py, placeholder vault
      gateway/              /v1/chat/completions, streaming re-hydrator, providers/, failover, cache
      ratelimit/            token bucket (Redis Lua script)
      audit/                hash-chained log, verify endpoint
      jobs/                 Redis Stream consumer (usage aggregation, audit fan-out)
      admin/                users, API keys, policies, usage
    tests/                  unit/, integration/, benchmark/
    benchmark/              synthetic German corpus + scorer
  web/                      React dashboard
  deploy/helm/docuvault/    chart (api Deployment, Service, ConfigMap, Secret, probes)
  docs/adr/                 short architecture decision records
  .github/workflows/        ci.yml, k8s-smoke.yml, keepwarm.yml
```

## CLAUDE.md constitution (to generate in Phase 0)

- Scope guard: features outside this plan need an ADR first.
- Layering: routers call services, services call repositories; no SQL in routers.
- Every schema change is an Alembic migration; no `create_all` outside tests.
- Every endpoint has OpenAPI response models, an auth dependency and an integration test.
- No secrets in the repo; config only through environment (`pydantic-settings`).
- Raw PII is never logged; logs carry placeholder tokens and request ids only.
- The app must pass all tests with `LLM_MODE=replay` (no network).
- Memory budget: API container under 400 MB resident with spaCy loaded.
- Commands section: `docker compose up`, `uv run pytest`, `ruff`, `mypy`, `helm lint`.

## Data model (Postgres)

`tenants`, `users` (role: admin / member / auditor / guest), `api_keys` (hashed, per tenant), `policies` (which PII types to redact per tenant), `requests` (metadata, provider, latency, token counts, cache hit), `redaction_events` (type, count, no raw values), `audit_log` (append-only, `prev_hash` + `hash`, UPDATE/DELETE revoked by trigger), `usage_daily` (aggregated by consumer). Indexes on `(tenant_id, created_at)` for requests and audit; documented in an ADR with `EXPLAIN` output.

Placeholder-to-original mappings live only in Redis with a short TTL, encrypted, never in Postgres.

## Phases (~45 h)

**Phase 0 — Scaffold (4 h).** Repo, CLAUDE.md, compose with Postgres + Redis, FastAPI health endpoint, Alembic baseline, ruff/mypy/pytest, CI running lint + tests.

**Phase 1 — Auth and tenancy (8 h).** Schema + migrations, JWT access/refresh, argon2, RBAC dependency, API keys, guest login that creates an ephemeral seeded tenant with TTL cleanup. Integration tests with testcontainers.

**Phase 2 — Redaction engine and benchmark (9 h).** Detector interface, checksum-validated detectors, spaCy NER detector, overlap resolution, stable placeholders (`<PERSON_1>`). Synthetic German corpus and scorer producing precision/recall per PII type; CI fails if scores regress; table published in README.

**Phase 3 — Gateway (10 h).** OpenAI-compatible `/v1/chat/completions`, non-streaming and SSE. Streaming re-hydrator that buffers across chunk boundaries so split placeholders are restored correctly (property-based tests). Provider abstraction, Gemini to Groq failover with circuit breaker, Redis response cache keyed on redacted prompt, replay mode, token-bucket rate limit per API key and per IP.

**Phase 4 — Audit, jobs, dashboard (8 h).** Hash-chained audit log with verify endpoint. Redis Stream consumer running inside the API process (Render free has no worker dynos), using long blocking reads to stay inside the Upstash command quota. Minimal dashboard: guest-login button, playground showing original / redacted / re-hydrated side by side, audit table, usage chart. Cold-start "waking up" state in the UI.

**Phase 5 — Ship (6 h).** Multi-stage image, Helm chart, `kind` workflow (`helm lint`, install, wait for ready, curl smoke test). Deploy to Render + Neon + Upstash + Cloudflare Pages. Scheduled keep-warm ping. JSON logs, `/metrics`, request ids. README with Mermaid diagram, GIF, benchmark table, coverage badge, honest limitations section. Hard daily LLM budget cap for guest tenants.

**Stretch (week 4, only if on schedule).** Document upload with `pgvector` similarity search and tenant isolation; OpenTelemetry tracing; Grafana in compose. Note: without this phase `pgvector` is not on the CV, so it is the first stretch item.

## Free-tier checks before Phase 5

Confirm current limits: Neon storage (believed 0.5 GB per project), Upstash monthly command cap, Render sleep behaviour, Gemini and Groq free quotas.

## Verification

- `docker compose up` from a clean clone gives a seeded, working app; guest login reaches the playground in under 30 seconds.
- `uv run pytest` passes with `LLM_MODE=replay`; coverage at or above 85 % on `app/`.
- Benchmark job prints per-type precision/recall and meets stored thresholds.
- Streaming test: placeholder split at every possible byte offset is re-hydrated correctly.
- Audit verify endpoint detects a manually tampered row.
- Rate-limit test: burst over the bucket returns `429` with `Retry-After`.
- Failover test: primary provider forced to fail, response served by fallback, then by replay.
- `kind` workflow green: pods ready, smoke request returns 200.
- `docker stats` shows API container under 400 MB.
- Live demo checked from a cold start in a private browser window.
