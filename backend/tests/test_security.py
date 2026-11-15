import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core import security

SECRET = "unit-test-secret-with-at-least-32-characters"


def _token(**overrides: object) -> str:
    arguments: dict[str, object] = {
        "user_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "role": "admin",
        "secret": SECRET,
        "ttl": timedelta(minutes=5),
    }
    arguments.update(overrides)
    return security.create_access_token(**arguments)  # type: ignore[arg-type]


def test_password_hash_round_trip() -> None:
    hashed = security.hash_password("s3cret")
    assert hashed.startswith("$argon2id$")
    assert security.verify_password("s3cret", hashed)
    assert not security.verify_password("wrong", hashed)


def test_verify_password_rejects_malformed_hash() -> None:
    assert not security.verify_password("anything", "not-a-hash")


def test_access_token_round_trip() -> None:
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = _token(user_id=user_id, tenant_id=tenant_id, role="auditor")
    assert security.decode_access_token(token, SECRET) == security.AccessClaims(
        user_id=user_id, tenant_id=tenant_id, role="auditor"
    )


def test_access_token_rejected_when_expired() -> None:
    token = _token(now=datetime.now(UTC) - timedelta(hours=1))
    assert security.decode_access_token(token, SECRET) is None


def test_access_token_rejected_with_wrong_secret() -> None:
    assert security.decode_access_token(_token(), "x" * 40) is None


def test_access_token_rejected_when_claims_missing() -> None:
    token = jwt.encode({"sub": str(uuid.uuid4()), "exp": 9_999_999_999}, SECRET, algorithm="HS256")
    assert security.decode_access_token(token, SECRET) is None


def test_access_token_rejected_when_subject_is_not_a_uuid() -> None:
    token = jwt.encode(
        {"sub": "42", "tid": "42", "role": "admin", "exp": 9_999_999_999}, SECRET, algorithm="HS256"
    )
    assert security.decode_access_token(token, SECRET) is None


def test_unsigned_token_rejected() -> None:
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "tid": str(uuid.uuid4()), "role": "admin", "exp": 9_999_999_999},
        key="",
        algorithm="none",
    )
    assert security.decode_access_token(token, SECRET) is None


def test_api_key_format_and_prefix() -> None:
    key = security.generate_api_key()
    assert key.plaintext.startswith(f"dv_{key.prefix}_")
    assert security.api_key_prefix(key.plaintext) == key.prefix
    assert key.digest == security.sha256_hex(key.plaintext)
    assert key.plaintext not in key.digest


def test_api_key_prefix_rejects_other_formats() -> None:
    for candidate in ("", "dv_", "dv_abc", "dv__secret", "sk_abc_secret", "eyJhbGciOi.payload.sig"):
        assert security.api_key_prefix(candidate) is None


def test_opaque_tokens_are_unique_and_stored_as_digest() -> None:
    first, first_digest = security.new_opaque_token()
    second, _ = security.new_opaque_token()
    assert first != second
    assert first_digest == security.sha256_hex(first)
