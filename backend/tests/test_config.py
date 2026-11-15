import pytest

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
