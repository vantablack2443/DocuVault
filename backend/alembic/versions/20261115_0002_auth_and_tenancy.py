"""auth and tenancy: tenants, users, refresh tokens, api keys

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column[sa.Uuid[str]]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "tenants",
        _id(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_guest", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_index(
        "ix_tenants_guest_expiry",
        "tenants",
        ["expires_at"],
        postgresql_where=sa.text("is_guest"),
    )

    op.create_table(
        "users",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "role IN ('admin', 'member', 'auditor')", name=op.f("ck_users_role_valid")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_users_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"])

    op.create_table(
        "refresh_tokens",
        _id(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"])

    op.create_table(
        "api_keys",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_api_keys_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_api_keys_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("prefix", name=op.f("uq_api_keys_prefix")),
    )
    op.create_index(op.f("ix_api_keys_tenant_id"), "api_keys", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
    op.drop_table("tenants")
