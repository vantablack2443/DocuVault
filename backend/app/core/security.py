"""Password hashing, access tokens, opaque tokens and API keys. No I/O in this module."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

JWT_ALGORITHM = "HS256"
API_KEY_SCHEME = "dv"

# OWASP minimum for Argon2id (19 MiB, 2 passes). The library default of 64 MiB per
# hash is too much for concurrent logins inside a 512 MB container.
_hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Verified against when the account does not exist, so response time does not
# reveal which e-mail addresses are registered.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str


def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
    secret: str,
    ttl: timedelta,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "role": role,
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, secret: str) -> AccessClaims | None:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "tid", "role"]},
        )
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tid"]),
            role=str(payload["role"]),
        )
    except (jwt.InvalidTokenError, ValueError):
        return None


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def digests_match(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def new_opaque_token() -> tuple[str, str]:
    """Return `(token, digest)`. Only the digest is stored."""
    token = secrets.token_urlsafe(48)
    return token, sha256_hex(token)


@dataclass(frozen=True)
class GeneratedApiKey:
    plaintext: str
    prefix: str
    digest: str


def generate_api_key() -> GeneratedApiKey:
    """Keys look like `dv_<prefix>_<secret>`. A fast hash is fine: the secret has 256 bits."""
    prefix = secrets.token_hex(4)
    plaintext = f"{API_KEY_SCHEME}_{prefix}_{secrets.token_urlsafe(32)}"
    return GeneratedApiKey(plaintext=plaintext, prefix=prefix, digest=sha256_hex(plaintext))


def api_key_prefix(plaintext: str) -> str | None:
    parts = plaintext.split("_", 2)
    if len(parts) != 3 or parts[0] != API_KEY_SCHEME or not parts[1] or not parts[2]:
        return None
    return parts[1]
