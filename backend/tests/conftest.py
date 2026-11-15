from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    application = create_app()
    async with LifespanManager(application):
        # Tests own Redis database 1; start each one with empty rate-limit counters.
        await application.state.redis.flushdb()
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as db:
        yield db
