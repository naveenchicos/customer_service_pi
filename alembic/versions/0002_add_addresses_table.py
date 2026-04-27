"""add addresses table and account default-address FKs

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-26 00:00:00.000000

Adds:
  - addresses table (with partial unique index on dedup_key for active rows)
  - accounts.default_shipping_address_id (FK → addresses.id, ON DELETE SET NULL)
  - accounts.default_billing_address_id  (FK → addresses.id, ON DELETE SET NULL)

The accounts ↔ addresses cycle is handled by emitting the FKs via ALTER after
both tables exist (use_alter pattern).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Create addresses table ─────────────────────────────────────────────
    op.create_table(
        "addresses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line1", sa.String(length=200), nullable=False),
        sa.Column("line2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=100), nullable=False),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=3), nullable=False),
        sa.Column("dedup_key", sa.String(length=150), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_addresses_account_id",
            ondelete="RESTRICT",
        ),
    )

    op.create_index("ix_addresses_account_id", "addresses", ["account_id"])
    op.create_index("ix_addresses_dedup_key", "addresses", ["dedup_key"])
    op.create_index("ix_addresses_status", "addresses", ["status"])

    # Partial unique index — at most one ACTIVE address per (account_id, dedup_key).
    # Soft-deleted rows ignored, so re-adding a previously deleted address works.
    op.create_index(
        "uq_addresses_account_dedup_active",
        "addresses",
        ["account_id", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # ── 2. Add FK columns to accounts (and constraints via ALTER) ─────────────
    op.add_column(
        "accounts",
        sa.Column(
            "default_shipping_address_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "default_billing_address_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_accounts_default_shipping_address_id",
        "accounts",
        "addresses",
        ["default_shipping_address_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_accounts_default_billing_address_id",
        "accounts",
        "addresses",
        ["default_billing_address_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Drop the account → addresses FK columns first, then the addresses table.
    op.drop_constraint(
        "fk_accounts_default_billing_address_id", "accounts", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_accounts_default_shipping_address_id", "accounts", type_="foreignkey"
    )
    op.drop_column("accounts", "default_billing_address_id")
    op.drop_column("accounts", "default_shipping_address_id")

    op.drop_index("uq_addresses_account_dedup_active", table_name="addresses")
    op.drop_index("ix_addresses_status", table_name="addresses")
    op.drop_index("ix_addresses_dedup_key", table_name="addresses")
    op.drop_index("ix_addresses_account_id", table_name="addresses")
    op.drop_table("addresses")
