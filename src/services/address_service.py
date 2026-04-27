"""
Address service — all business logic for the addresses API.

Routers are thin; they validate input and call this service.

Error codes used in raised ValueError:
  ACCOUNT_NOT_FOUND          — 404
  ADDRESS_NOT_FOUND          — 404
  ADDRESS_LIMIT_EXCEEDED     — 409 (max 10 active addresses per account)
  ADDRESS_DUPLICATE          — 409 (matching dedup_key for an active address)
  COUNTRY_NOT_SUPPORTED      — 400 (country missing from country_rules)
  NO_FIELDS_TO_UPDATE        — 422 (PATCH body had nothing to apply)
"""

import logging
import uuid
from typing import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.country_rules import UnsupportedCountryError, get_country_rule
from src.models.account import Account
from src.models.address import Address, AddressStatus
from src.schemas.address import AddressCreate, AddressUpdate

logger = logging.getLogger(__name__)

MAX_ACTIVE_ADDRESSES_PER_ACCOUNT = 10


# ── Helpers ───────────────────────────────────────────────────────────────────


def _norm(v: str | None) -> str:
    """Lowercase + strip — used when building the dedup key."""
    return (v or "").strip().lower()


def build_dedup_key(
    line1: str,
    line2: str | None,
    city: str,
    state: str,
    postal_code: str,
    country: str,
) -> str:
    """
    Deterministic dedup key for an address.

    Formula (Scenario B): line1[:5] | line2[:5] | city | state | postal_code[:N]
    where N = country rule's postal_code_dedup_length (defaults to len(postal_code)
    if shorter). All segments lowercased + whitespace-stripped. NULL line2 → empty.
    """
    rule = get_country_rule(country)
    pc = _norm(postal_code)
    pc_segment = (
        pc[: rule.postal_code_dedup_length]
        if len(pc) >= rule.postal_code_dedup_length
        else pc
    )

    return "|".join(
        [
            _norm(line1)[:5],
            _norm(line2)[:5],
            _norm(city),
            _norm(state),
            pc_segment,
        ]
    )


def _validate_country(country: str) -> None:
    """Raises ValueError('COUNTRY_NOT_SUPPORTED') if the country is unsupported."""
    try:
        rule = get_country_rule(country)
    except UnsupportedCountryError as exc:
        raise ValueError("COUNTRY_NOT_SUPPORTED") from exc

    # No-op reference so linters don't drop the import; rule is used by build_dedup_key.
    _ = rule


def _validate_postal_code(country: str, postal_code: str) -> None:
    """Raises ValueError('COUNTRY_NOT_SUPPORTED') if postal code doesn't match country pattern."""
    rule = get_country_rule(country)
    if not rule.postal_code_pattern.match(postal_code):
        # Misuse of COUNTRY_NOT_SUPPORTED would be misleading here; raise a distinct code.
        raise ValueError("POSTAL_CODE_INVALID")


async def _get_account_or_raise(db: AsyncSession, account_id: uuid.UUID) -> Account:
    stmt = select(Account).where(Account.id == account_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("ACCOUNT_NOT_FOUND")
    return account


async def _count_active_addresses(db: AsyncSession, account_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Address)
        .where(Address.account_id == account_id, Address.status == AddressStatus.ACTIVE)
    )
    result = await db.execute(stmt)
    return int(result.scalar_one())


async def _find_active_dedup(
    db: AsyncSession,
    account_id: uuid.UUID,
    dedup_key: str,
    exclude_id: uuid.UUID | None = None,
) -> Address | None:
    stmt = select(Address).where(
        Address.account_id == account_id,
        Address.dedup_key == dedup_key,
        Address.status == AddressStatus.ACTIVE,
    )
    if exclude_id is not None:
        stmt = stmt.where(Address.id != exclude_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _apply_default_pointers(
    db: AsyncSession,
    account_id: uuid.UUID,
    address_id: uuid.UUID,
    caller_identity: str | None,
) -> None:
    """Set both default_shipping_address_id and default_billing_address_id on the account."""
    stmt = (
        update(Account)
        .where(Account.id == account_id)
        .values(
            default_shipping_address_id=address_id,
            default_billing_address_id=address_id,
            updated_by=caller_identity,
        )
    )
    await db.execute(stmt)


async def _clear_default_pointers_to(
    db: AsyncSession,
    account_id: uuid.UUID,
    address_id: uuid.UUID,
    caller_identity: str | None,
) -> None:
    """
    When an address is soft-deleted, null out any default pointers that reference it.
    ON DELETE SET NULL only fires on hard delete; this handles the soft-delete path.
    """
    account = await db.get(Account, account_id)
    if account is None:
        return
    changed = False
    if account.default_shipping_address_id == address_id:
        account.default_shipping_address_id = None
        changed = True
    if account.default_billing_address_id == address_id:
        account.default_billing_address_id = None
        changed = True
    if changed:
        account.updated_by = caller_identity


# ── Create ────────────────────────────────────────────────────────────────────


async def create_address(
    db: AsyncSession,
    account_id: uuid.UUID,
    payload: AddressCreate,
    caller_identity: str | None = None,
) -> Address:
    """
    Create a new address for ``account_id``.

    Enforces:
      - account exists
      - country is supported (else COUNTRY_NOT_SUPPORTED)
      - postal_code matches country pattern (else POSTAL_CODE_INVALID)
      - max 10 active addresses per account (else ADDRESS_LIMIT_EXCEEDED)
      - no duplicate active address by dedup_key (else ADDRESS_DUPLICATE)
      - if billing_same_as_shipping → both account default FKs point to this address
    """
    await _get_account_or_raise(db, account_id)
    _validate_country(payload.country)
    _validate_postal_code(payload.country, payload.postal_code)

    if (
        await _count_active_addresses(db, account_id)
        >= MAX_ACTIVE_ADDRESSES_PER_ACCOUNT
    ):
        raise ValueError("ADDRESS_LIMIT_EXCEEDED")

    dedup_key = build_dedup_key(
        line1=payload.line1,
        line2=payload.line2,
        city=payload.city,
        state=payload.state,
        postal_code=payload.postal_code,
        country=payload.country,
    )

    existing = await _find_active_dedup(db, account_id, dedup_key)
    if existing is not None:
        logger.info(
            "Address creation rejected — duplicate",
            extra={"account_id": str(account_id), "existing_id": str(existing.id)},
        )
        raise ValueError(f"ADDRESS_DUPLICATE:{existing.id}")

    address = Address(
        account_id=account_id,
        line1=payload.line1,
        line2=payload.line2,
        city=payload.city,
        state=payload.state,
        postal_code=payload.postal_code,
        country=payload.country.upper(),
        dedup_key=dedup_key,
        status=AddressStatus.ACTIVE,
        created_by=caller_identity,
        updated_by=caller_identity,
    )
    db.add(address)
    await db.flush()  # populate address.id

    if payload.billing_same_as_shipping:
        await _apply_default_pointers(db, account_id, address.id, caller_identity)

    await db.refresh(address)
    return address


# ── Read ──────────────────────────────────────────────────────────────────────


async def get_address(
    db: AsyncSession, account_id: uuid.UUID, address_id: uuid.UUID
) -> Address:
    stmt = select(Address).where(
        Address.id == address_id, Address.account_id == account_id
    )
    result = await db.execute(stmt)
    address = result.scalar_one_or_none()
    if address is None:
        raise ValueError("ADDRESS_NOT_FOUND")
    return address


async def list_addresses(db: AsyncSession, account_id: uuid.UUID) -> Iterable[Address]:
    """Return all ACTIVE addresses for an account (no pagination — capped at 10)."""
    await _get_account_or_raise(db, account_id)
    stmt = (
        select(Address)
        .where(
            Address.account_id == account_id,
            Address.status == AddressStatus.ACTIVE,
        )
        .order_by(Address.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Update ────────────────────────────────────────────────────────────────────


async def update_address(
    db: AsyncSession,
    account_id: uuid.UUID,
    address_id: uuid.UUID,
    payload: AddressUpdate,
    caller_identity: str | None = None,
) -> Address:
    """
    Partial update. Recomputes ``dedup_key`` from the merged values and re-runs
    the duplicate check (excluding this row). Honours ``billing_same_as_shipping``.
    """
    address = await get_address(db, account_id, address_id)

    changes = payload.model_dump(exclude_none=True)
    flag = bool(changes.pop("billing_same_as_shipping", False))

    if not changes and not flag:
        raise ValueError("NO_FIELDS_TO_UPDATE")

    # Apply changes to a working copy of the values used for dedup recompute
    new_country = changes.get("country", address.country)
    if "country" in changes:
        _validate_country(new_country)
    new_postal = changes.get("postal_code", address.postal_code)
    if "country" in changes or "postal_code" in changes:
        _validate_postal_code(new_country, new_postal)

    if changes:
        for key, value in changes.items():
            setattr(address, key, value.upper() if key == "country" else value)

        address.dedup_key = build_dedup_key(
            line1=address.line1,
            line2=address.line2,
            city=address.city,
            state=address.state,
            postal_code=address.postal_code,
            country=address.country,
        )
        address.updated_by = caller_identity

        existing = await _find_active_dedup(
            db, account_id, address.dedup_key, exclude_id=address.id
        )
        if existing is not None:
            raise ValueError(f"ADDRESS_DUPLICATE:{existing.id}")

    if flag:
        await _apply_default_pointers(db, account_id, address.id, caller_identity)

    await db.flush()
    await db.refresh(address)
    return address


# ── Soft delete ───────────────────────────────────────────────────────────────


async def soft_delete_address(
    db: AsyncSession,
    account_id: uuid.UUID,
    address_id: uuid.UUID,
    caller_identity: str | None = None,
) -> Address:
    """Sets status=inactive and nulls any account default pointer to this address."""
    address = await get_address(db, account_id, address_id)
    address.status = AddressStatus.INACTIVE
    address.updated_by = caller_identity
    await _clear_default_pointers_to(db, account_id, address.id, caller_identity)
    await db.flush()
    await db.refresh(address)
    return address
