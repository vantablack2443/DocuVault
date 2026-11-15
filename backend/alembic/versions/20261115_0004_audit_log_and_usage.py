"""audit log (hash chain, append-only trigger) and daily usage

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Rows may only disappear together with their tenant (expired guest sandboxes).
# During that cascade the tenant row is already gone, which is what the
# function checks. Everything else, including every UPDATE, is rejected.
IMMUTABLE_FUNCTION = """
CREATE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM tenants WHERE id = OLD.tenant_id) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql
"""

IMMUTABLE_TRIGGER = """
CREATE TRIGGER audit_log_immutable
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()
"""


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_audit_log_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        sa.UniqueConstraint("tenant_id", "seq", name=op.f("uq_audit_log_tenant_id")),
        sa.UniqueConstraint("source_id", name=op.f("uq_audit_log_source_id")),
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(IMMUTABLE_TRIGGER)

    op.create_table(
        "usage_daily",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("cache_hits", sa.Integer(), nullable=False),
        sa.Column("redactions", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_usage_daily_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "day", name=op.f("pk_usage_daily")),
    )


def downgrade() -> None:
    op.drop_table("usage_daily")
    op.drop_table("audit_log")
    op.execute("DROP FUNCTION audit_log_immutable()")
