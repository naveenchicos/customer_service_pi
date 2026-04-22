"""
Account service — all business logic lives here.

Routers are thin; they validate input and call this service.
This layer owns: DB queries, customer enrichment, audit field population,
and domain-level error raising.

Error codes used in HTTPException detail:
  ACCOUNT_NOT_FOUND        — 404
  ACCOUNT_ALREADY_EXISTS   — 409 (duplicate customer_number or email)
  NO_FIELDS_TO_UPDATE      — 422 (PATCH body was effectively empty)
"""

import logging
import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.clients.customer_client import get_customer_or_none
from src.models.account import Account, AccountStatus
from src.schemas.account import AccountCreate, AccountUpdate

logger = logging.getLogger(__name__)


# ── Create ────────────────────────────────────────────────────────────────────

async def create_account(
    db: AsyncSession,
    payload: AccountCreate,
    caller_identity: str | None = None,
) -> Account:
    """
    Create a new account.

    Normalises email to lowercase before persisting (prevents duplicate
    accounts that differ only in case — OWASP A03).

    Raises:
        ValueError("ACCOUNT_ALREADY_EXISTS") on duplicate customer_number or email.
    """
    account = Account(
        customer_number=payload.customer_number.upper(),
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email.lower(),
        phone=payload.phone,
        status=AccountStatus.ACTIVE,
        created_by=caller_identity,
        updated_by=caller_identity,
    )
    db.add(account)
    try:
        await db.flush()  # surface DB errors before commit
    except IntegrityError as exc:
        await db.rollback()
        logger.info(
            "Account creation rejected — duplicate key",
            extra={"customer_number": payload.customer_number, "error": str(exc)},
        )
        raise ValueError("ACCOUNT_ALREADY_EXISTS") from exc
    await db.refresh(account)
    return account


# ── Read ──────────────────────────────────────────────────────────────────────

async def get_account_by_id(db: AsyncSession, account_id: uuid.UUID) -> Account:
    """
    Fetch a single account by internal UUID.

    Raises:
        ValueError("ACCOUNT_NOT_FOUND") when not found.
    """
    stmt = select(Account).where(Account.id == account_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("ACCOUNT_NOT_FOUND")
    return account


async def get_account_by_customer_number(
    db: AsyncSession, customer_number: str
) -> Account:
    """
    Fetch a single account by customer number.

    Raises:
        ValueError("ACCOUNT_NOT_FOUND") when not found.
    """
    stmt = select(Account).where(
        Account.customer_number == customer_number.upper()
    )
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("ACCOUNT_NOT_FOUND")
    return account


async def search_accounts(
    db: AsyncSession,
    query: str | None = None,
    status: AccountStatus | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Account], int]:
    """
    Search accounts with optional free-text and status filter.

    Free-text search is case-insensitive and matches against:
      first_name, last_name, email, customer_number

    Returns (items, total_count).
    """
    page_size = min(page_size, 100)  # cap at 100 to prevent resource exhaustion
    offset = (page - 1) * page_size

    base_stmt = select(Account)
    count_stmt = select(func.count()).select_from(Account)

    if status:
        base_stmt = base_stmt.where(Account.status == status)
        count_stmt = count_stmt.where(Account.status == status)

    if query:
        # Use ILIKE for case-insensitive substring search — parameterised by SQLAlchemy,
        # preventing SQL injection (OWASP A03)
        pattern = f"%{query}%"
        text_filter = or_(
            Account.first_name.ilike(pattern),
            Account.last_name.ilike(pattern),
            Account.email.ilike(pattern),
            Account.customer_number.ilike(pattern),
        )
        base_stmt = base_stmt.where(text_filter)
        count_stmt = count_stmt.where(text_filter)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    base_stmt = (
        base_stmt
        .order_by(Account.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = await db.execute(base_stmt)
    accounts = list(rows.scalars().all())

    return accounts, total


# ── Update ────────────────────────────────────────────────────────────────────

async def update_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    payload: AccountUpdate,
    caller_identity: str | None = None,
) -> Account:
    """
    Partial update (PATCH semantics) — only non-None fields in payload are applied.

    Raises:
        ValueError("ACCOUNT_NOT_FOUND") when not found.
        ValueError("NO_FIELDS_TO_UPDATE") when payload contains no changes.
        ValueError("ACCOUNT_ALREADY_EXISTS") on duplicate email.
    """
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise ValueError("NO_FIELDS_TO_UPDATE")

    if "email" in changes:
        changes["email"] = changes["email"].lower()

    changes["updated_by"] = caller_identity

    stmt = (
        update(Account)
        .where(Account.id == account_id)
        .values(**changes)
        .returning(Account)
    )
    try:
        result = await db.execute(stmt)
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError("ACCOUNT_ALREADY_EXISTS") from exc

    updated = result.scalar_one_or_none()
    if updated is None:
        raise ValueError("ACCOUNT_NOT_FOUND")
    return updated


# ── Soft delete ───────────────────────────────────────────────────────────────

async def deactivate_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    caller_identity: str | None = None,
) -> Account:
    """
    Soft-delete by setting status=inactive.
    Physical deletes are not supported to preserve audit history.

    Raises:
        ValueError("ACCOUNT_NOT_FOUND") when not found.
    """
    stmt = (
        update(Account)
        .where(Account.id == account_id)
        .values(status=AccountStatus.INACTIVE, updated_by=caller_identity)
        .returning(Account)
    )
    result = await db.execute(stmt)
    updated = result.scalar_one_or_none()
    if updated is None:
        raise ValueError("ACCOUNT_NOT_FOUND")
    return updated


# ── Enrichment ────────────────────────────────────────────────────────────────

async def get_account_with_customer_details(
    db: AsyncSession,
    account_id: uuid.UUID,
) -> tuple[Account, dict | None]:
    """
    Returns (account, customer_details).
    customer_details is None if the Customer Service circuit is open or returns 404.
    Failures do not propagate — the account is always returned.
    """
    account = await get_account_by_id(db, account_id)
    customer_details = await get_customer_or_none(account.customer_number)
    return account, customer_details
