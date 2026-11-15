import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin import router as admin_router
from app.api import health
from app.auth import router as auth_router
from app.core.config import get_settings
from app.core.errors import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
)
from app.core.logging import configure_logging
from app.gateway import router as gateway_router
from app.gateway.failover import AllProvidersFailedError, build_chain
from app.ratelimit.token_bucket import TokenBucket
from app.redaction import router as redaction_router
from app.redaction.engine import build_engine

log = structlog.get_logger()

REQUEST_ID_HEADER = "X-Request-ID"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.redis = redis
    app.state.redaction_engine = build_engine(use_ner=settings.ner_enabled)
    http = httpx.AsyncClient(timeout=httpx.Timeout(settings.provider_timeout_seconds, connect=5.0))
    app.state.settings = settings
    app.state.provider_chain = build_chain(settings, http)
    app.state.token_bucket = TokenBucket(
        redis,
        capacity=settings.rate_limit_capacity,
        refill_per_second=settings.rate_limit_refill_per_second,
    )
    log.info("startup", environment=settings.environment, llm_mode=settings.llm_mode)
    try:
        yield
    finally:
        await http.aclose()
        await redis.aclose()
        await engine.dispose()
        log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="DocuVault",
        version="0.1.0",
        summary="LLM gateway with German PII minimisation before external LLM calls",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth_router.router)
    app.include_router(admin_router.router)
    app.include_router(gateway_router.router)
    app.include_router(redaction_router.router)
    return app


def register_error_handlers(app: FastAPI) -> None:
    """Map domain errors to HTTP. Messages stay generic: no hint about what exactly failed."""

    @app.exception_handler(AuthenticationError)
    async def unauthorized(_: Request, __: AuthenticationError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Invalid or missing credentials"},
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(PermissionDeniedError)
    async def forbidden(_: Request, __: PermissionDeniedError) -> JSONResponse:
        return JSONResponse({"detail": "Insufficient role"}, status_code=status.HTTP_403_FORBIDDEN)

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, __: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=status.HTTP_404_NOT_FOUND)

    @app.exception_handler(AllProvidersFailedError)
    async def upstream_unavailable(_: Request, __: AllProvidersFailedError) -> JSONResponse:
        return JSONResponse(
            {"detail": "No upstream provider available"}, status_code=status.HTTP_502_BAD_GATEWAY
        )

    @app.exception_handler(RateLimitedError)
    async def rate_limited(_: Request, error: RateLimitedError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Too many requests"},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(error.retry_after_seconds)},
        )


app = create_app()
