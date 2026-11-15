import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr, ValidationError

from app import seed
from app.core.config import Settings


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@host/db",
        "postgresql://u:p@host/db",
        "postgresql+asyncpg://u:p@host/db",
    ],
)
def test_database_url_always_uses_asyncpg(raw: str) -> None:
    assert Settings(database_url=raw).database_url == "postgresql+asyncpg://u:p@host/db"


def test_production_refuses_missing_or_weak_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(environment="production")
    with pytest.raises(ValidationError):
        Settings(environment="production", jwt_secret=SecretStr("too-short"))
    assert Settings(environment="production", jwt_secret=SecretStr("r" * 32))


async def test_seed_creates_admin_once_and_admin_can_log_in(
    app: FastAPI, client: AsyncClient
) -> None:
    email = f"seed-{uuid.uuid4().hex[:8]}@example.test"
    settings = Settings(seed_admin_email=email.upper(), seed_admin_password=SecretStr("seed-pw"))
    factory = app.state.session_factory

    assert await seed.seed(factory, settings) is True
    assert await seed.seed(factory, settings) is False

    response = await client.post("/auth/login", json={"email": email, "password": "seed-pw"})
    assert response.status_code == 200


async def test_seed_does_nothing_without_credentials(app: FastAPI) -> None:
    settings = Settings(seed_admin_email=None, seed_admin_password=None)
    assert await seed.seed(app.state.session_factory, settings) is False


async def test_seed_entrypoint_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEED_ADMIN_EMAIL", "")
    await seed.main()
