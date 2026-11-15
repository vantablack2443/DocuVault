import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role
from tests.factories import auth_headers, create_user

IBAN = "DE89370400440532013000"
TEXT = f"Frau Schmidt bittet um Überweisung auf {IBAN}, Rückfragen an a.schmidt@example.de."


async def test_preview_returns_redacted_text_and_offsets(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = auth_headers(await create_user(session, role=Role.MEMBER))

    response = await client.post("/redaction/preview", json={"text": TEXT}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["redacted_text"] == (
        "Frau <PERSON_1> bittet um Überweisung auf <IBAN_1>, Rückfragen an <EMAIL_1>."
    )
    assert body["counts"] == {"PERSON": 1, "IBAN": 1, "EMAIL": 1}
    iban = next(entity for entity in body["entities"] if entity["type"] == "IBAN")
    assert TEXT[iban["start"] : iban["end"]] == IBAN
    assert iban["detector"] == "iban_checksum"
    # Offsets and placeholders only: the response never repeats the raw values.
    assert IBAN not in response.text


async def test_preview_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post("/redaction/preview", json={"text": TEXT})).status_code == 401


async def test_preview_validates_length(client: AsyncClient, session: AsyncSession) -> None:
    headers = auth_headers(await create_user(session))
    empty = await client.post("/redaction/preview", json={"text": ""}, headers=headers)
    huge = await client.post("/redaction/preview", json={"text": "a" * 20_001}, headers=headers)
    assert (empty.status_code, huge.status_code) == (422, 422)


async def test_raw_pii_never_reaches_the_logs(
    client: AsyncClient, session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    headers = auth_headers(await create_user(session))
    capsys.readouterr()

    await client.post("/redaction/preview", json={"text": TEXT}, headers=headers)

    logs = capsys.readouterr().out
    assert "redaction_preview" in logs
    for secret in (IBAN, "Schmidt", "a.schmidt@example.de"):
        assert secret not in logs
