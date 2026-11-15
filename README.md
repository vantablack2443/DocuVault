# DocuVault

OpenAI-compatible LLM gateway that minimises German PII before prompts reach an
external LLM, re-hydrates the response, and keeps a tamper-evident audit log.

> **Status: Phase 0 of 5 (scaffold).** Health endpoints, database migrations, CI.
> No redaction, auth or gateway yet. Roadmap: [docs/PLAN.md](docs/PLAN.md).

## Run locally

```bash
docker compose up --build
```

- API: http://localhost:8000
- OpenAPI docs: http://localhost:8000/docs
- Liveness / readiness: `/healthz`, `/readyz`

## Checks

```bash
docker compose --profile test run --rm --build test
```

Runs `ruff`, `mypy --strict`, the Alembic migrations and `pytest` (85 % coverage
gate) against real PostgreSQL and Redis containers.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL 17 + pgvector ·
Redis 7 · structlog · Docker · GitHub Actions
