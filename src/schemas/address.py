"""
Pydantic schemas for the Addresses API.

Input validation enforces OWASP A03 (Injection prevention):
  - String lengths are bounded.
  - Country must be ISO 3166-1 alpha-3 (3 uppercase letters).
  - No control characters in any text field.

Internal-only fields (``dedup_key``) are NEVER exposed in responses.
``address_type`` is intentionally absent — addresses are typeless; "preferred
shipping/billing" semantics live on the parent account via
``default_shipping_address_id`` / ``default_billing_address_id``.
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.address import AddressStatus

_LINE_MAX = 200
_CITY_MAX = 100
_STATE_MAX = 100
_POSTAL_MAX = 20
_COUNTRY_RE = re.compile(r"^[A-Z]{3}$")


def _no_control_chars(v: str) -> str:
    if any(ord(c) < 32 for c in v):
        raise ValueError("Field must not contain control characters")
    return v


# ── Request schemas ───────────────────────────────────────────────────────────


class AddressCreate(BaseModel):
    """Payload for POST /accounts/{id}/addresses."""

    model_config = ConfigDict(str_strip_whitespace=True)

    line1: str = Field(
        ..., min_length=1, max_length=_LINE_MAX, examples=["123 Main St"]
    )
    line2: str | None = Field(default=None, max_length=_LINE_MAX, examples=["Apt 3A"])
    city: str = Field(..., min_length=1, max_length=_CITY_MAX, examples=["Boston"])
    state: str = Field(..., min_length=1, max_length=_STATE_MAX, examples=["MA"])
    postal_code: str = Field(
        ..., min_length=1, max_length=_POSTAL_MAX, examples=["02101"]
    )
    country: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="ISO 3166-1 alpha-3 country code (e.g. USA)",
        examples=["USA"],
    )
    billing_same_as_shipping: bool = Field(
        default=False,
        description=(
            "When true, sets BOTH default_shipping_address_id and "
            "default_billing_address_id on the parent account to this address."
        ),
    )

    @field_validator("country")
    @classmethod
    def country_format(cls, v: str) -> str:
        upper = v.upper()
        if not _COUNTRY_RE.match(upper):
            raise ValueError("country must be ISO 3166-1 alpha-3 (3 letters, e.g. USA)")
        return upper

    @field_validator("line1", "line2", "city", "state", "postal_code")
    @classmethod
    def no_control_chars(cls, v: str | None) -> str | None:
        return _no_control_chars(v) if v is not None else v


class AddressUpdate(BaseModel):
    """
    Payload for PATCH /accounts/{id}/addresses/{address_id}.
    All fields optional — only provided fields are updated.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    line1: str | None = Field(default=None, min_length=1, max_length=_LINE_MAX)
    line2: str | None = Field(default=None, max_length=_LINE_MAX)
    city: str | None = Field(default=None, min_length=1, max_length=_CITY_MAX)
    state: str | None = Field(default=None, min_length=1, max_length=_STATE_MAX)
    postal_code: str | None = Field(default=None, min_length=1, max_length=_POSTAL_MAX)
    country: str | None = Field(default=None, min_length=3, max_length=3)
    billing_same_as_shipping: bool = Field(
        default=False,
        description=(
            "When true, sets BOTH default_shipping_address_id and "
            "default_billing_address_id on the parent account to this address."
        ),
    )

    @field_validator("country")
    @classmethod
    def country_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        upper = v.upper()
        if not _COUNTRY_RE.match(upper):
            raise ValueError("country must be ISO 3166-1 alpha-3 (3 letters, e.g. USA)")
        return upper

    @field_validator("line1", "line2", "city", "state", "postal_code")
    @classmethod
    def no_control_chars(cls, v: str | None) -> str | None:
        return _no_control_chars(v) if v is not None else v


# ── Response schema ───────────────────────────────────────────────────────────


class AddressResponse(BaseModel):
    """Address representation returned by all read/write endpoints.

    Excludes ``dedup_key`` (internal) and any ``address_type`` (not stored).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    line1: str
    line2: str | None
    city: str
    state: str
    postal_code: str
    country: str
    status: AddressStatus
    created_at: datetime
    updated_at: datetime
