import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.db import repositories as repo
from app.db.models import RefreshToken, Role, Tenant, User
from app.ratelimit.fixed_window import enforce_fixed_window

log = structlog.get_logger()


@dataclass(frozen=True)
class Principal:
    """Authenticated human user, resolved from an access token."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role
    is_guest: bool


@dataclass(frozen=True)
class ApiKeyPrincipal:
    """Authenticated machine caller, resolved from an API key."""

    api_key_id: uuid.UUID
    tenant_id: uuid.UUID
    is_guest: bool


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int


def normalise_email(email: str) -> str:
    return email.strip().lower()


def _tenant_expired(tenant: Tenant, now: datetime) -> bool:
    return tenant.expires_at is not None and tenant.expires_at <= now


def _access_token(user: User, settings: Settings, ttl: timedelta) -> str:
    return security.create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        secret=settings.jwt_secret.get_secret_value(),
        ttl=ttl,
    )


def _issue_token_pair(session: AsyncSession, user: User, settings: Settings) -> IssuedTokens:
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    refresh_token, digest = security.new_opaque_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    return IssuedTokens(
        access_token=_access_token(user, settings, ttl),
        refresh_token=refresh_token,
        expires_in=int(ttl.total_seconds()),
    )


async def login(
    session: AsyncSession, settings: Settings, *, email: str, password: str
) -> IssuedTokens:
    user = await repo.get_user_by_email(session, normalise_email(email))
    stored_hash = user.password_hash if user and user.password_hash else None
    # Argon2 is CPU-bound: keep it off the event loop. Always run it, so unknown
    # accounts take as long as wrong passwords.
    password_ok = await asyncio.to_thread(
        security.verify_password, password, stored_hash or security.DUMMY_PASSWORD_HASH
    )
    if user is None or stored_hash is None or not password_ok or not user.is_active:
        log.info("login_failed")
        raise AuthenticationError
    tokens = _issue_token_pair(session, user, settings)
    await session.commit()
    log.info("login_succeeded", user_id=str(user.id), tenant_id=str(user.tenant_id))
    return tokens


async def refresh(session: AsyncSession, settings: Settings, *, refresh_token: str) -> IssuedTokens:
    """Rotate a refresh token. Presenting an already-used token revokes the whole family."""
    now = datetime.now(UTC)
    stored = await repo.get_refresh_token_for_update(session, security.sha256_hex(refresh_token))
    if stored is None:
        raise AuthenticationError
    if stored.revoked_at is not None:
        await repo.revoke_all_refresh_tokens(session, stored.user_id, now)
        await session.commit()
        log.warning("refresh_token_reuse_detected", user_id=str(stored.user_id))
        raise AuthenticationError
    if stored.expires_at <= now:
        raise AuthenticationError
    found = await repo.get_user_with_tenant(session, stored.user_id)
    if found is None or not found[0].is_active or _tenant_expired(found[1], now):
        raise AuthenticationError
    stored.revoked_at = now
    tokens = _issue_token_pair(session, found[0], settings)
    await session.commit()
    return tokens


async def logout(session: AsyncSession, *, refresh_token: str) -> None:
    """Idempotent: unknown or already revoked tokens are not an error."""
    stored = await repo.get_refresh_token_for_update(session, security.sha256_hex(refresh_token))
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)
        await session.commit()


async def guest_login(
    session: AsyncSession, redis: Redis, settings: Settings, *, client_ip: str
) -> IssuedTokens:
    """Create a throwaway sandbox tenant and sign the caller in as its admin.

    No refresh token: the session simply ends when the sandbox expires.
    """
    await enforce_fixed_window(
        redis,
        f"ratelimit:guest-login:{client_ip}",
        limit=settings.guest_logins_per_hour,
        window_seconds=3600,
    )
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.guest_tenant_ttl_minutes)
    await repo.delete_expired_guest_tenants(session, now)

    tenant = Tenant(id=uuid.uuid4(), name="Musterfirma GmbH (Demo)", is_guest=True)
    tenant.expires_at = now + ttl
    slug = tenant.id.hex[:12]
    users = [
        User(id=uuid.uuid4(), tenant_id=tenant.id, role=role, email=f"{name}@{slug}.demo.invalid")
        for name, role in (
            ("anna.schmidt", Role.ADMIN),
            ("jonas.weber", Role.MEMBER),
            ("revision", Role.AUDITOR),
        )
    ]
    session.add(tenant)
    await session.flush()
    session.add_all(users)
    await session.commit()
    log.info("guest_tenant_created", tenant_id=str(tenant.id))
    return IssuedTokens(
        access_token=_access_token(users[0], settings, ttl),
        refresh_token=None,
        expires_in=int(ttl.total_seconds()),
    )


async def authenticate_access_token(
    session: AsyncSession, settings: Settings, token: str
) -> Principal:
    claims = security.decode_access_token(token, settings.jwt_secret.get_secret_value())
    if claims is None:
        raise AuthenticationError
    found = await repo.get_user_with_tenant(session, claims.user_id)
    if found is None:
        raise AuthenticationError
    user, tenant = found
    if not user.is_active or _tenant_expired(tenant, datetime.now(UTC)):
        raise AuthenticationError
    # Role and tenant come from the database, not the token, so a demotion or
    # deactivation takes effect immediately instead of at token expiry.
    return Principal(
        user_id=user.id,
        tenant_id=tenant.id,
        email=user.email,
        role=Role(user.role),
        is_guest=tenant.is_guest,
    )


async def authenticate_api_key(session: AsyncSession, plaintext: str) -> ApiKeyPrincipal:
    prefix = security.api_key_prefix(plaintext)
    if prefix is None:
        raise AuthenticationError
    found = await repo.get_api_key_with_tenant_by_prefix(session, prefix)
    if found is None:
        raise AuthenticationError
    api_key, tenant = found
    now = datetime.now(UTC)
    if (
        not security.digests_match(api_key.key_hash, security.sha256_hex(plaintext))
        or api_key.revoked_at is not None
        or _tenant_expired(tenant, now)
    ):
        raise AuthenticationError
    # At most one write per key per minute, not one per request.
    if api_key.last_used_at is None or now - api_key.last_used_at > timedelta(minutes=1):
        api_key.last_used_at = now
        await session.commit()
    return ApiKeyPrincipal(api_key_id=api_key.id, tenant_id=tenant.id, is_guest=tenant.is_guest)
