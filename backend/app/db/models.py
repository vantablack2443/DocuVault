import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, false, func, text, true
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
