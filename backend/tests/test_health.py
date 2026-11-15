from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_with_real_dependencies(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


async def test_readiness_reports_failed_dependencies(app: FastAPI, client: AsyncClient) -> None:
    app.state.engine = create_async_engine("postgresql+asyncpg://nobody:x@127.0.0.1:1/none")
    app.state.redis = Redis.from_url("redis://127.0.0.1:1/0")

    response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"postgres": "fail", "redis": "fail"},
    }


async def test_request_id_is_generated_and_echoed(client: AsyncClient) -> None:
    generated = await client.get("/healthz")
    assert len(generated.headers["X-Request-ID"]) == 32

    echoed = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["X-Request-ID"] == "abc-123"


async def test_baseline_migration_enabled_pgvector(app: FastAPI) -> None:
    async with app.state.engine.connect() as connection:
        result = await connection.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
    assert result.scalar() == 1
