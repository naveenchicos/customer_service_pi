"""
Addresses router — REST endpoints for address management.

Endpoints (all nested under an account):
  POST   /accounts/{account_id}/addresses                  Add an address
  GET    /accounts/{account_id}/addresses                  List active addresses (max 10)
  GET    /accounts/{account_id}/addresses/{address_id}     Get one
  PATCH  /accounts/{account_id}/addresses/{address_id}     Partial update
  DELETE /accounts/{account_id}/addresses/{address_id}     Soft-delete
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.schemas.account import ErrorDetail
from src.schemas.address import AddressCreate, AddressResponse, AddressUpdate
from src.services import address_service

router = APIRouter(prefix="/accounts/{account_id}/addresses", tags=["Addresses"])

# ── Error code → HTTP status mapping ─────────────────────────────────────────
_ERROR_STATUS: dict[str, int] = {
    "ACCOUNT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ADDRESS_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ADDRESS_LIMIT_EXCEEDED": status.HTTP_409_CONFLICT,
    "ADDRESS_DUPLICATE": status.HTTP_409_CONFLICT,
    "COUNTRY_NOT_SUPPORTED": status.HTTP_400_BAD_REQUEST,
    "POSTAL_CODE_INVALID": status.HTTP_400_BAD_REQUEST,
    "NO_FIELDS_TO_UPDATE": status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _http_exc(request: Request, raw_code: str) -> HTTPException:
    """
    Build a consistent HTTPException with the ErrorDetail body.

    Handles ``ADDRESS_DUPLICATE:<existing_id>`` by stripping the id into the message
    so the client can recover the existing address without an extra round-trip.
    """
    code, _, suffix = raw_code.partition(":")
    http_status = _ERROR_STATUS.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    if code == "ADDRESS_DUPLICATE" and suffix:
        message = f"An active address with the same key already exists (id={suffix})"
    else:
        message = code.replace("_", " ").capitalize()

    return HTTPException(
        status_code=http_status,
        detail=ErrorDetail(
            code=code,
            message=message,
            correlation_id=getattr(request.state, "correlation_id", None),
        ).model_dump(),
    )


# ── POST /accounts/{account_id}/addresses ────────────────────────────────────


@router.post(
    "",
    response_model=AddressResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an address to an account",
    response_description="The newly created address",
    responses={
        400: {
            "model": ErrorDetail,
            "description": "Unsupported country or invalid postal code",
        },
        404: {"model": ErrorDetail, "description": "Account not found"},
        409: {
            "model": ErrorDetail,
            "description": "Duplicate address or 10-address limit reached",
        },
        422: {"model": ErrorDetail, "description": "Validation error in request body"},
    },
)
async def create_address(
    account_id: uuid.UUID,
    payload: AddressCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AddressResponse:
    """
    Create a new address attached to an account.

    Behaviour:
      - At most 10 active addresses per account.
      - Duplicate detection: same building/apartment/city/state/zip = 409 Conflict.
      - When **billing_same_as_shipping** is true, both
        `default_shipping_address_id` and `default_billing_address_id` on the
        parent account are set to this address — overwriting any existing values.
    """
    caller = request.headers.get("X-Caller-Identity")
    try:
        address = await address_service.create_address(
            db, account_id, payload, caller_identity=caller
        )
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AddressResponse.model_validate(address)


# ── GET /accounts/{account_id}/addresses ─────────────────────────────────────


@router.get(
    "",
    response_model=list[AddressResponse],
    summary="List active addresses for an account",
    response_description="All active addresses (max 10) for the account",
    responses={
        404: {"model": ErrorDetail, "description": "Account not found"},
    },
)
async def list_addresses(
    account_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[AddressResponse]:
    """List all active addresses for the account, ordered by creation time (oldest first)."""
    try:
        addresses = await address_service.list_addresses(db, account_id)
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return [AddressResponse.model_validate(a) for a in addresses]


# ── GET /accounts/{account_id}/addresses/{address_id} ────────────────────────


@router.get(
    "/{address_id}",
    response_model=AddressResponse,
    summary="Get a specific address",
    responses={
        404: {"model": ErrorDetail, "description": "Account or address not found"},
    },
)
async def get_address(
    account_id: uuid.UUID,
    address_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AddressResponse:
    """Fetch a single address by its UUID."""
    try:
        address = await address_service.get_address(db, account_id, address_id)
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AddressResponse.model_validate(address)


# ── PATCH /accounts/{account_id}/addresses/{address_id} ──────────────────────


@router.patch(
    "/{address_id}",
    response_model=AddressResponse,
    summary="Partially update an address",
    responses={
        400: {
            "model": ErrorDetail,
            "description": "Unsupported country or invalid postal code",
        },
        404: {"model": ErrorDetail, "description": "Account or address not found"},
        409: {"model": ErrorDetail, "description": "Duplicate address after update"},
        422: {"model": ErrorDetail, "description": "No updatable fields provided"},
    },
)
async def update_address(
    account_id: uuid.UUID,
    address_id: uuid.UUID,
    payload: AddressUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AddressResponse:
    """
    Partial update.

    - Only fields present in the body are modified.
    - **billing_same_as_shipping=true** repoints both account default FKs to
      this address (same semantics as on create).
    """
    caller = request.headers.get("X-Caller-Identity")
    try:
        address = await address_service.update_address(
            db, account_id, address_id, payload, caller_identity=caller
        )
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AddressResponse.model_validate(address)


# ── DELETE /accounts/{account_id}/addresses/{address_id} ─────────────────────


@router.delete(
    "/{address_id}",
    response_model=AddressResponse,
    summary="Soft-delete an address",
    response_description="The deleted address (status=inactive)",
    responses={
        404: {"model": ErrorDetail, "description": "Account or address not found"},
    },
)
async def soft_delete_address(
    account_id: uuid.UUID,
    address_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AddressResponse:
    """
    Soft-delete by setting status=inactive.

    Also clears any default-shipping or default-billing pointer on the parent
    account that references this address.
    """
    caller = request.headers.get("X-Caller-Identity")
    try:
        address = await address_service.soft_delete_address(
            db, account_id, address_id, caller_identity=caller
        )
    except ValueError as exc:
        raise _http_exc(request, str(exc)) from exc
    return AddressResponse.model_validate(address)
