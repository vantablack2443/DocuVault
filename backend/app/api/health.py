from typing import Literal

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter(tags=["health"])
log = structlog.get_logger()

CheckResult = Literal["ok", "fail"]


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckResult]


async def check_database(engine: AsyncEngine) -> CheckResult:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as error:
        log.warning("readiness_check_failed", dependency="postgres", error=type(error).__name__)
        return "fail"
    return "ok"


async def check_redis(redis: Redis) -> CheckResult:
    try:
        await redis.ping()
    except Exception as error:
        log.warning("readiness_check_failed", dependency="redis", error=type(error).__name__)
        return "fail"
    return "ok"


@router.get("/healthz", response_model=Liveness, summary="Liveness probe")
async def healthz() -> Liveness:
    """Process is up. Touches no dependencies, so it stays cheap for keep-warm pings."""
    return Liveness()


@router.get(
    "/readyz",
    response_model=Readiness,
    summary="Readiness probe",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
async def readyz(request: Request, response: Response) -> Readiness:
    """Postgres and Redis are reachable."""
    checks: dict[str, CheckResult] = {
        "postgres": await check_database(request.app.state.engine),
        "redis": await check_redis(request.app.state.redis),
    }
    healthy = all(result == "ok" for result in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ok" if healthy else "degraded", checks=checks)
