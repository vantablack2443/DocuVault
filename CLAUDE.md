# DocuVault — project constitution

OpenAI-compatible LLM gateway that redacts German PII before prompts leave the
network, re-hydrates responses, and keeps a tamper-evident audit log.

The full phased plan lives in `docs/PLAN.md`. Current phase: **0 (scaffold) done, 1 (auth and tenancy) next**.

## Hard rules

1. **Scope guard.** Anything not in `docs/PLAN.md` needs a short ADR in `docs/adr/` before code.
2. **Layering.** Routers call services, services call repositories. No SQL in routers.
3. **Migrations only.** Every schema change is an Alembic migration. No `create_all` outside tests.
4. **Every endpoint** has OpenAPI response models, an auth dependency (except health and login), and an integration test.
5. **No secrets in the repo.** Configuration only through environment variables (`app/core/config.py`).
6. **Raw PII is never logged or stored in Postgres.** Logs carry placeholder tokens and request ids only.
7. **Offline-testable.** All tests pass with `LLM_MODE=replay` and no outbound network.
8. **Memory budget.** API container stays under 400 MB resident (Render free tier is 512 MB). No transformer models.
9. **Honest wording.** Never write "DSGVO-compliant" or "sovereign". Write "PII minimisation before external LLM calls".
10. **Zero cost.** No service that needs a credit card. No paid managed Kubernetes.

## Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async, asyncpg), Alembic,
redis-py (asyncio), structlog, httpx. Dependencies managed with `uv`
(`backend/pyproject.toml`, `backend/uv.lock`). PostgreSQL 17 with `pgvector`, Redis 7.

## Commands

The host does not need Python or `uv`; everything runs in containers.

```bash
docker compose up --build                     # API on http://localhost:8000, docs at /docs
docker compose --profile test run --rm test   # ruff, mypy, pytest with coverage
docker compose down -v                        # stop and wipe volumes
```

Regenerate the lockfile after editing dependencies:

```bash
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/backend:/app" -w /app ghcr.io/astral-sh/uv:python3.12-bookworm-slim uv lock
```

New migration (with the stack running):

```bash
docker compose run --rm api alembic revision -m "describe change"
```

## Layout

```
backend/app/core/   config, logging, security
backend/app/db/     engine, models, repositories
backend/app/api/    routers
backend/alembic/    migrations
backend/tests/      pytest (needs Postgres + Redis from compose or CI services)
docs/adr/           architecture decision records
```

## Conventions

- `ruff` for lint and format, `mypy --strict`; both must be clean before commit.
- Coverage gate: 85 % on `app/`.
- Structured JSON logs through `structlog`; bind context, do not format strings.
- Commit messages: Conventional Commits (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).
