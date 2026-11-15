# DocuVault — project constitution

OpenAI-compatible LLM gateway that redacts German PII before prompts leave the
network, re-hydrates responses, and keeps a tamper-evident audit log.

The full phased plan lives in `docs/PLAN.md`. Current phase: **5 built locally (hardening, Helm chart, workflows, deploy config); actual deployment and first CI run are open (`docs/DEPLOY.md`). Stretch items not started.**

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
docker compose up --build                     # API on :8000 (docs at /docs), dashboard on :5173
docker compose --profile test run --rm test   # ruff, mypy, pytest with coverage
docker compose down -v                        # stop and wipe volumes
cd web && npm run check && npm test && npm run build   # dashboard: typecheck, unit tests, build
cd web && npm run dev                         # dashboard with hot reload (needs Node 20)
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
backend/app/gateway/   /v1 endpoints, pipeline (service.py), providers, failover, cache, stream re-hydrator
backend/app/ratelimit/ Redis rate limiting
backend/app/audit/     hash-chained audit log, verify endpoint
backend/app/jobs/      Redis Stream event bus and in-process consumer
backend/app/redaction/ detectors, NER, engine, placeholder vault, preview endpoint
backend/benchmark/     synthetic corpus, scorer, thresholds (`python -m benchmark --check`)
backend/app/seed.py    local admin seed
backend/alembic/    migrations
backend/tests/      pytest (needs Postgres + Redis from compose or CI services)
web/                React dashboard (Vite, TypeScript): `src/api.ts` client, `src/text.ts` pure helpers, `src/views/`
deploy/helm/        Helm chart (`values-kind.yaml` for the CI smoke test)
render.yaml         Render blueprint; docs/DEPLOY.md has the steps and free-tier limits
docs/adr/           architecture decision records
```

## Conventions

- `ruff` for lint and format, `mypy --strict`; both must be clean before commit.
- Coverage gate: 85 % on `app/`.
- Structured JSON logs through `structlog`; bind context, do not format strings.
- Auth: humans use JWT access tokens (`PrincipalDep`, `require_roles(...)`); machines use `dv_…` API keys (`ApiKeyPrincipalDep`). Roles: admin, member, auditor. Guest access is a sandbox tenant (ADR 0001).
- Services raise domain errors from `app/core/errors.py`; `app/main.py` maps them to HTTP. Services commit; repositories never do.
- `alembic check` runs in CI: models and migrations must agree.
- Redaction: never tune detectors on `HELD_OUT_TEMPLATES`. Leak rate is the gate that matters; a precision gain that raises it is a regression. NER memory rules are in ADR 0002.
- Gateway: the placeholder vault is per request and never persisted (ADR 0003). Providers and the cache only ever see redacted text. Tests use `tests/fakes.py::FakeProvider`; no test may call a real LLM.
- Audit: call `audit.append(...)` inside the transaction of the change it describes, before the commit. `details` takes ids, counts and enum values only. Never UPDATE or DELETE `audit_log` (ADR 0004).
- Tests share one persistent database: use unique ids and e-mails, never fixed ones. After `session.rollback()` reload objects before reading attributes.
- Commit messages: Conventional Commits (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).
- Dashboard: no UI or chart library, no font CDN (fonts are bundled). Pure logic goes in `web/src/text.ts` with a Vitest test. UI copy follows hard rule 9: no "never leaks" claims.
- Metrics labels: route templates and enum values only. Never tenant ids, raw paths or anything from request content.
- Client address for rate limits: always `app.core.network.client_ip`, never `request.client.host` directly.
- Guest tenants: any new code path that calls a live provider must go through `gateway.service._restriction` (daily guest budget).
- Helm chart check without local tools: `docker run --rm -v "$PWD/deploy/helm:/charts" -w /charts alpine/helm:3.17.3 lint docuvault -f docuvault/values-kind.yaml`
- Dashboard in production calls the API as `/api/...` on its own address; `web/worker.js` forwards (ADR 0005). Never point the production build at the `onrender.com` address: content blockers stop it.
- Pushes that touch only `web/` or docs do not redeploy the API (`buildFilter` in `render.yaml`).
- Security workflow (`.github/workflows/security.yml`): pip-audit, npm audit, gitleaks, Trivy, CodeQL. A gitleaks false positive goes into `.gitleaksignore` with a comment saying why; never weaken a rule globally.
- Any code that builds a URL or path from request input needs a test with `//host`, `\\` and encoded variants (see `web/worker.test.ts`).
