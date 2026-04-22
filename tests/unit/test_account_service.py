"""
Unit tests for account service business logic.

Uses AsyncMock database sessions — no real database required.
Tests verify domain rules: normalisation, soft-delete, duplicate detection,
partial update semantics, and search pagination cap.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from src.models.account import Account, AccountStatus
from src.schemas.account import AccountCreate, AccountUpdate
from src.services import account_service


def _mock_account(**kwargs) -> Account:
    """Build a minimal Account ORM object for testing."""
    defaults = {
        "id": uuid.uuid4(),
        "customer_number": "CUST-001",
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane.smith@example.com",
        "phone": None,
        "status": AccountStatus.ACTIVE,
        "created_by": None,
        "updated_by": None,
    }
    obj = MagicMock(spec=Account)
    for k, v in {**defaults, **kwargs}.items():
        setattr(obj, k, v)
    return obj


# ── create_account ────────────────────────────────────────────────────────────

class TestCreateAccount:
    @pytest.mark.asyncio
    async def test_email_normalised_to_lowercase(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        payload = AccountCreate(
            customer_number="CUST-001",
            first_name="Jane",
            last_name="Smith",
            email="Jane.Smith@EXAMPLE.COM",
        )

        await account_service.create_account(db, payload, caller_identity="ci")

        # Inspect the Account object added to the session
        added = db.add.call_args[0][0]
        assert added.email == "jane.smith@example.com"

    @pytest.mark.asyncio
    async def test_customer_number_uppercased(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        payload = AccountCreate(
            customer_number="cust-001",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

        await account_service.create_account(db, payload)

        added = db.add.call_args[0][0]
        assert added.customer_number == "CUST-001"

    @pytest.mark.asyncio
    async def test_raises_on_integrity_error(self):
        db = AsyncMock()
        db.flush = AsyncMock(side_effect=IntegrityError("duplicate", {}, None))
        db.rollback = AsyncMock()

        payload = AccountCreate(
            customer_number="CUST-001",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

        with pytest.raises(ValueError, match="ACCOUNT_ALREADY_EXISTS"):
            await account_service.create_account(db, payload)


# ── get_account_by_id ─────────────────────────────────────────────────────────

class TestGetAccountById:
    @pytest.mark.asyncio
    async def test_returns_account_when_found(self):
        account = _mock_account()
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=account))
        )

        result = await account_service.get_account_by_id(db, account.id)

        assert result is account

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        with pytest.raises(ValueError, match="ACCOUNT_NOT_FOUND"):
            await account_service.get_account_by_id(db, uuid.uuid4())


# ── update_account ────────────────────────────────────────────────────────────

class TestUpdateAccount:
    @pytest.mark.asyncio
    async def test_raises_when_no_fields_provided(self):
        db = AsyncMock()

        with pytest.raises(ValueError, match="NO_FIELDS_TO_UPDATE"):
            await account_service.update_account(db, uuid.uuid4(), AccountUpdate())

    @pytest.mark.asyncio
    async def test_email_normalised_on_update(self):
        account = _mock_account()
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=account))
        )

        payload = AccountUpdate(email="UPDATED@EXAMPLE.COM")
        await account_service.update_account(db, account.id, payload)

        call_kwargs = db.execute.call_args[0][0]
        # Verify the UPDATE statement includes normalised email
        compiled = call_kwargs.compile(compile_kwargs={"literal_binds": True})
        assert "updated@example.com" in str(compiled)

    @pytest.mark.asyncio
    async def test_raises_not_found_when_id_missing(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        with pytest.raises(ValueError, match="ACCOUNT_NOT_FOUND"):
            await account_service.update_account(
                db, uuid.uuid4(), AccountUpdate(first_name="New")
            )


# ── deactivate_account ────────────────────────────────────────────────────────

class TestDeactivateAccount:
    @pytest.mark.asyncio
    async def test_raises_not_found_when_id_missing(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        with pytest.raises(ValueError, match="ACCOUNT_NOT_FOUND"):
            await account_service.deactivate_account(db, uuid.uuid4())


# ── search_accounts ───────────────────────────────────────────────────────────

class TestSearchAccounts:
    @pytest.mark.asyncio
    async def test_page_size_capped_at_100(self):
        db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[count_result, rows_result])

        await account_service.search_accounts(db, page_size=500)

        # Second execute call is the paginated query — check LIMIT is capped
        paginated_stmt = db.execute.call_args_list[1][0][0]
        compiled = str(paginated_stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT 100" in compiled
