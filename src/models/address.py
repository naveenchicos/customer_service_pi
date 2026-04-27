"""
Address ORM model.

Maps to the `addresses` table in PostgreSQL. One account → many addresses.
Soft-delete via `status`; physical deletes are not supported (audit trail).

Notes
-----
- ``dedup_key`` is computed in the service layer (see address_service.build_dedup_key)
  and never exposed via the API. It backs a partial unique index that prevents
  duplicate active addresses per account.
- The FK to accounts uses ON DELETE RESTRICT — accounts are soft-deleted, so
  this constraint is a defensive safety net against accidental physical deletes.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base

if TYPE_CHECKING:
    from src.models.account import Account


class AddressStatus(str, PyEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Address(Base):
    __tablename__ = "addresses"

    # ── Identity ──────────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # ── Address data ──────────────────────────────────────────────────────────
    line1: Mapped[str] = mapped_column(String(200), nullable=False)
    line2: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        comment="ISO 3166-1 alpha-3 (e.g. USA, GBR, IND)",
    )

    # Internal — used for duplicate detection. Never returned in API responses.
    dedup_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        index=True,
        comment=(
            "Service-computed: line1[:5]|line2[:5]|city|state|postal_code[:5], "
            "lowercased+stripped"
        ),
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[AddressStatus] = mapped_column(
        String(20),
        nullable=False,
        default=AddressStatus.ACTIVE,
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
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    account: Mapped["Account"] = relationship(
        "Account",
        back_populates="addresses",
        foreign_keys=[account_id],
    )

    # ── Constraints / indexes ─────────────────────────────────────────────────
    # Partial unique index: at most one active address per (account_id, dedup_key).
    # Soft-deleted rows are ignored, so re-adding a previously deleted address works.
    __table_args__ = (
        Index(
            "uq_addresses_account_dedup_active",
            "account_id",
            "dedup_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Address id={self.id} account_id={self.account_id} "
            f"city={self.city!r} status={self.status}>"
        )
