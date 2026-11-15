# DocuVault

OpenAI-compatible LLM gateway that minimises German PII before prompts reach an
external LLM, re-hydrates the response, and keeps a tamper-evident audit log.

> **Status: Phase 1 of 5 (auth and tenancy).** JWT login with refresh rotation, RBAC,
> API keys, guest sandbox tenants. No redaction or LLM gateway yet. Roadmap: [docs/PLAN.md](docs/PLAN.md).

## Run locally

```bash
docker compose up --build
```

- API: http://localhost:8000
- OpenAPI docs: http://localhost:8000/docs
- Liveness / readiness: `/healthz`, `/readyz`
- Guest sandbox (no sign-up): `curl -X POST http://localhost:8000/auth/guest`
- Local admin: `admin@beispiel.test` / `local-demo-password` (compose only)

## Checks

```bash
docker compose --profile test run --rm --build test
```

Runs `ruff`, `mypy --strict`, the Alembic migrations and `pytest` (85 % coverage
gate) against real PostgreSQL and Redis containers.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL 17 + pgvector ·
Redis 7 · structlog · Docker · GitHub Actions
