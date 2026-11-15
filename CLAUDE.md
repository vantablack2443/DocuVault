# DocuVault — project constitution

OpenAI-compatible LLM gateway that redacts German PII before prompts leave the
network, re-hydrates responses, and keeps a tamper-evident audit log.

The full phased plan lives in `docs/PLAN.md`. Current phase: **1 (auth and tenancy) done, 2 (redaction engine and benchmark) next**.

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
backend/app/core/      config, logging, security primitives, domain errors
backend/app/db/        models, session dependency, repositories (all SQL)
backend/app/api/       health probes
backend/app/auth/      login, refresh, guest sandbox, RBAC dependencies
backend/app/admin/     users and API keys
backend/app/gateway/   OpenAI-compatible /v1 endpoints (API-key auth)
backend/app/ratelimit/ Redis rate limiting
backend/app/seed.py    local admin seed
backend/alembic/    migrations
backend/tests/      pytest (needs Postgres + Redis from compose or CI services)
docs/adr/           architecture decision records
```

## Conventions

- `ruff` for lint and format, `mypy --strict`; both must be clean before commit.
- Coverage gate: 85 % on `app/`.
- Structured JSON logs through `structlog`; bind context, do not format strings.
- Auth: humans use JWT access tokens (`PrincipalDep`, `require_roles(...)`); machines use `dv_…` API keys (`ApiKeyPrincipalDep`). Roles: admin, member, auditor. Guest access is a sandbox tenant (ADR 0001).
- Services raise domain errors from `app/core/errors.py`; `app/main.py` maps them to HTTP. Services commit; repositories never do.
- `alembic check` runs in CI: models and migrations must agree.
- Commit messages: Conventional Commits (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).
