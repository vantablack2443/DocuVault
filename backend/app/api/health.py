import secrets
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.schemas import ErrorResponse
from app.core import metrics
from app.core.config import SettingsDep
from app.core.errors import AuthenticationError, NotFoundError

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


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
    responses={
        status.HTTP_200_OK: {"content": {"text/plain": {}}, "description": "Text format"},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def prometheus_metrics(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> Response:
    """Off unless `METRICS_TOKEN` is set; then it needs that token as a bearer token.

    The numbers are operational, not personal, but request volumes of a public
    demo are still nobody else's business.
    """
    if settings.metrics_token is None:
        raise NotFoundError
    expected = f"Bearer {settings.metrics_token.get_secret_value()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise AuthenticationError
    return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)
