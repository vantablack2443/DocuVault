import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Role(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"
    AUDITOR = "auditor"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        # Guest cleanup scans only sandbox tenants; a partial index keeps it tiny.
        Index("ix_tenants_guest_expiry", "expires_at", postgresql_where=text("is_guest")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(120))
    is_guest: Mapped[bool] = mapped_column(default=False, server_default=false())
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'member', 'auditor')", name="role_valid"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(254), unique=True)
    # NULL for sandbox users, who can only enter through guest login.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(80))
    # Public lookup handle; the secret part is only ever stored as a SHA-256 digest.
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Policy(Base):
    """Which PII types a tenant redacts. No row means: all of them."""

    __tablename__ = "policies"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    enabled_types: Mapped[list[str]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class RequestLog(Base):
    """Metadata of one gateway call. Never holds prompt or response text."""

    __tablename__ = "requests"
    __table_args__ = (
        # Dashboards and the audit view read "latest calls of my tenant".
        Index("ix_requests_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    model: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    stream: Mapped[bool]
    cache_hit: Mapped[bool]
    latency_ms: Mapped[int]
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    # {"IBAN": 2, "PERSON": 1}: how many values of each type were replaced.
    redaction_counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AuditEntry(Base):
    """One link of a tenant's hash chain. Append-only: a trigger rejects UPDATE and DELETE."""

    __tablename__ = "audit_log"
    # Also the index behind "latest entries of my tenant".
    __table_args__ = (UniqueConstraint("tenant_id", "seq"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    seq: Mapped[int]
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, object]] = mapped_column(JSONB)
    # Set by the application, not the database: the value is part of the hash.
    created_at: Mapped[datetime]
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    # Redis Stream id of the event this entry came from; makes redelivery harmless.
    source_id: Mapped[str | None] = mapped_column(String(32), unique=True)


class UsageDaily(Base):
    __tablename__ = "usage_daily"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(primary_key=True)
    requests: Mapped[int] = mapped_column(default=0)
    cache_hits: Mapped[int] = mapped_column(default=0)
    redactions: Mapped[int] = mapped_column(default=0)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
