"""create accounts table

Revision ID: 0001
Revises:
Create Date: 2026-04-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("customer_number", sa.String(length=50), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=True),
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
        sa.UniqueConstraint("customer_number"),
        sa.UniqueConstraint("email"),
    )

    # Indexes defined on the model
    op.create_index("ix_accounts_customer_number", "accounts", ["customer_number"])
    op.create_index("ix_accounts_email", "accounts", ["email"])
    op.create_index("ix_accounts_status", "accounts", ["status"])
    op.create_index(
        "ix_accounts_last_name_first_name", "accounts", ["last_name", "first_name"]
    )
    op.create_index(
        "ix_accounts_status_created_at", "accounts", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_accounts_status_created_at", table_name="accounts")
    op.drop_index("ix_accounts_last_name_first_name", table_name="accounts")
    op.drop_index("ix_accounts_status", table_name="accounts")
    op.drop_index("ix_accounts_email", table_name="accounts")
    op.drop_index("ix_accounts_customer_number", table_name="accounts")
    op.drop_table("accounts")
