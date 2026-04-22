"""
Account ORM model.

Maps to the `accounts` table in PostgreSQL.
Uses UUIDs as primary keys (no sequential IDs exposed externally — OWASP A01).
Soft-delete via `status` field; records are never physically deleted.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class AccountStatus(str, PyEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    # ── Identity ──────────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    customer_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="Business-facing unique identifier assigned at account creation",
    )

    # ── Personal details ──────────────────────────────────────────────────────
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="Stored as-is; normalisation (lowercasing) done in the service layer",
    )
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[AccountStatus] = mapped_column(
        String(20),
        nullable=False,
        default=AccountStatus.ACTIVE,
        server_default="active",
        index=True,
    )

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=text("now()"),
    )
    created_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Identity of the caller that created this record (from API key context)",
    )
    updated_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Identity of the caller that last modified this record",
    )

    # ── Composite index for common search patterns ────────────────────────────
    __table_args__ = (
        Index("ix_accounts_last_name_first_name", "last_name", "first_name"),
        Index("ix_accounts_status_created_at", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Account id={self.id} customer_number={self.customer_number!r} "
            f"email={self.email!r} status={self.status}>"
        )
