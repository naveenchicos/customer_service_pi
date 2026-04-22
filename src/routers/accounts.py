"""
Accounts router — REST endpoints for account management.

REST conventions:
  POST   /accounts                        Create account
  GET    /accounts/{account_id}           Get by internal UUID
  GET    /accounts/by-customer/{number}   Get by customer number (explicit sub-resource)
  GET    /accounts                        Search / list with pagination
  PATCH  /accounts/{account_id}           Partial update
  DELETE /accounts/{account_id}           Soft-delete (sets status=inactive)

All endpoints echo X-Correlation-ID back in the response (set by middleware).
Error responses always use the ErrorDetail schema — never raw exception messages.
"""

import uuid
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.account import AccountStatus
from src.schemas.account import (
    AccountCreate,
    AccountResponse,
    AccountSummary,
    AccountUpdate,
    ErrorDetail,
    PaginatedAccounts,
)
from src.services import account_service

router = APIRouter(prefix="/accounts", tags=["Accounts"])

# ── Error code → HTTP status mapping ─────────────────────────────────────────
_ERROR_STATUS: dict[str, int] = {
    "ACCOUNT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ACCOUNT_ALREADY_EXISTS": status.HTTP_409_CONFLICT,
    "NO_FIELDS_TO_UPDATE": status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _http_exc(request: Request, code: str, message: str | None = None) -> HTTPException:
    """Build a consistent HTTPException with the ErrorDetail body."""
    http_status = _ERROR_STATUS.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return HTTPException(
        status_code=http_status,
        detail=ErrorDetail(
            code=code,
            message=message or code.replace("_", " ").capitalize(),
            correlation_id=getattr(request.state, "correlation_id", None),
        ).model_dump(),
    )


# ── POST /accounts ─────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new account",
    response_description="The newly created account",
    responses={
        409: {
            "model": ErrorDetail,
            "description": "Customer number or email already exists",
        },
        422: {"model": ErrorDetail, "description": "Validation error in request body"},
    },
)
async def create_account(
    payload: AccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Create a new customer account.

    - **customer_number** must be unique across all accounts.
    - **email** must be unique and is normalised to lowercase on storage.
    - Returns **201 Created** with the full account representation.
    """
    caller = request.headers.get("X-Caller-Identity")
    try:
        account = await account_service.create_account(
            db, payload, caller_identity=caller
        )
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AccountResponse.model_validate(account)


# ── GET /accounts ──────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=PaginatedAccounts,
    summary="Search and list accounts",
    response_description="Paginated list of accounts matching the search criteria",
    responses={
        422: {"model": ErrorDetail, "description": "Invalid query parameters"},
    },
)
async def list_accounts(
    request: Request,
    query: str | None = Query(
        default=None,
        max_length=200,
        description="Free-text search across first_name, last_name, email, customer_number",
        examples=["Smith"],
    ),
    status_filter: AccountStatus | None = Query(
        default=None,
        alias="status",
        description="Filter by account status",
    ),
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Items per page (max 100)"
    ),
    db: AsyncSession = Depends(get_db),
) -> PaginatedAccounts:
    """
    List and search accounts with pagination.

    - Use **query** for case-insensitive substring search.
    - Use **status** to filter by account lifecycle state.
    - Results are ordered by **created_at** descending (newest first).
    """
    accounts, total = await account_service.search_accounts(
        db, query=query, status=status_filter, page=page, page_size=page_size
    )
    pages = ceil(total / page_size) if total > 0 else 1
    return PaginatedAccounts(
        items=[AccountSummary.model_validate(a) for a in accounts],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


# ── GET /accounts/{account_id} ─────────────────────────────────────────────


@router.get(
    "/{account_id}",
    response_model=AccountResponse,
    summary="Get account by ID",
    response_description="Full account details",
    responses={
        404: {"model": ErrorDetail, "description": "Account not found"},
    },
)
async def get_account(
    account_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Retrieve a single account by its internal UUID.

    Prefer `/accounts/by-customer/{customer_number}` for lookups from business systems.
    """
    try:
        account = await account_service.get_account_by_id(db, account_id)
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AccountResponse.model_validate(account)


# ── GET /accounts/by-customer/{customer_number} ────────────────────────────


@router.get(
    "/by-customer/{customer_number}",
    response_model=AccountResponse,
    summary="Get account by customer number",
    response_description="Full account details",
    responses={
        404: {"model": ErrorDetail, "description": "Account not found"},
    },
)
async def get_account_by_customer_number(
    customer_number: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Look up an account by its business-facing customer number.

    Customer numbers are case-insensitive; `CUST-001` and `cust-001` resolve to the same account.
    """
    try:
        account = await account_service.get_account_by_customer_number(
            db, customer_number
        )
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AccountResponse.model_validate(account)


# ── PATCH /accounts/{account_id} ───────────────────────────────────────────


@router.patch(
    "/{account_id}",
    response_model=AccountResponse,
    summary="Partially update an account",
    response_description="Updated account",
    responses={
        404: {"model": ErrorDetail, "description": "Account not found"},
        409: {
            "model": ErrorDetail,
            "description": "Email already in use by another account",
        },
        422: {"model": ErrorDetail, "description": "No updatable fields provided"},
    },
)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Partially update an account (PATCH semantics).

    Only fields present in the request body are modified.
    Omitting a field leaves it unchanged — do not pass `null` to clear optional fields.
    """
    caller = request.headers.get("X-Caller-Identity")
    try:
        account = await account_service.update_account(
            db, account_id, payload, caller_identity=caller
        )
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AccountResponse.model_validate(account)


# ── DELETE /accounts/{account_id} ──────────────────────────────────────────


@router.delete(
    "/{account_id}",
    response_model=AccountResponse,
    summary="Deactivate an account",
    response_description="Deactivated account (status set to inactive)",
    responses={
        404: {"model": ErrorDetail, "description": "Account not found"},
    },
)
async def deactivate_account(
    account_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Soft-delete an account by setting its status to **inactive**.

    Physical deletion is not supported to preserve audit history.
    Use `PATCH /accounts/{id}` with `{"status": "suspended"}` to suspend instead.
    """
    caller = request.headers.get("X-Caller-Identity")
    try:
        account = await account_service.deactivate_account(
            db, account_id, caller_identity=caller
        )
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AccountResponse.model_validate(account)
