"""
Pydantic schemas for the Accounts API.

Separate schema classes are used for each operation so that the shape
of a request body, response, and update never silently bleed into each other.

Input validation rules enforce OWASP A03 (Injection prevention):
  - String lengths are bounded
  - email is validated by email-validator
  - phone accepts only a safe character set
  - No raw SQL or HTML accepted in any field
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.models.account import AccountStatus

# ── Shared field constraints ──────────────────────────────────────────────────
_NAME_MAX = 100
_PHONE_RE = re.compile(r"^[+\d\s\-().]{7,30}$")
_CUSTOMER_NUMBER_RE = re.compile(r"^[A-Za-z0-9\-]{3,50}$")


# ── Request schemas ───────────────────────────────────────────────────────────


class AccountCreate(BaseModel):
    """Payload for POST /accounts."""

    model_config = ConfigDict(str_strip_whitespace=True)

    customer_number: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="Unique business identifier (alphanumeric and hyphens only)",
        examples=["CUST-00123"],
    )
    first_name: str = Field(
        ...,
        min_length=1,
        max_length=_NAME_MAX,
        description="Account holder first name",
        examples=["Jane"],
    )
    last_name: str = Field(
        ...,
        min_length=1,
        max_length=_NAME_MAX,
        description="Account holder last name",
        examples=["Smith"],
    )
    email: EmailStr = Field(
        ...,
        description="Primary email address (must be unique across accounts)",
        examples=["jane.smith@example.com"],
    )
    phone: str | None = Field(
        default=None,
        description="Contact phone number in any international format",
        examples=["+1-800-555-0100"],
    )

    @field_validator("customer_number")
    @classmethod
    def customer_number_format(cls, v: str) -> str:
        if not _CUSTOMER_NUMBER_RE.match(v):
            raise ValueError(
                "customer_number may only contain letters, digits, and hyphens (3-50 chars)"
            )
        return v.upper()

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v: str | None) -> str | None:
        if v is not None and not _PHONE_RE.match(v):
            raise ValueError(
                "phone must be 7-30 characters and contain only digits, spaces, "
                "parentheses, hyphens, dots, or a leading +"
            )
        return v

    @field_validator("first_name", "last_name")
    @classmethod
    def name_no_control_chars(cls, v: str) -> str:
        if any(ord(c) < 32 for c in v):
            raise ValueError("Name fields must not contain control characters")
        return v


class AccountUpdate(BaseModel):
    """
    Payload for PATCH /accounts/{id}.
    All fields optional — only provided fields are updated (true partial update).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=_NAME_MAX,
        examples=["Janet"],
    )
    last_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=_NAME_MAX,
        examples=["Smith-Jones"],
    )
    email: EmailStr | None = Field(
        default=None,
        examples=["janet.smith@example.com"],
    )
    phone: str | None = Field(
        default=None,
        examples=["+44 20 7946 0958"],
    )
    status: AccountStatus | None = Field(
        default=None,
        description="Account lifecycle status",
        examples=["inactive"],
    )

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v: str | None) -> str | None:
        if v is not None and not _PHONE_RE.match(v):
            raise ValueError(
                "phone must be 7-30 characters and contain only digits, spaces, "
                "parentheses, hyphens, dots, or a leading +"
            )
        return v


# ── Response schemas ──────────────────────────────────────────────────────────


class AccountResponse(BaseModel):
    """Full account representation returned by all read/write endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Internal UUID — use customer_number for lookups")
    customer_number: str
    first_name: str
    last_name: str
    email: str
    phone: str | None
    status: AccountStatus
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


class AccountSummary(BaseModel):
    """Lightweight projection used in paginated list results."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_number: str
    first_name: str
    last_name: str
    email: str
    status: AccountStatus
    created_at: datetime


# ── Pagination ────────────────────────────────────────────────────────────────


class PaginatedAccounts(BaseModel):
    """Envelope for paginated list responses."""

    items: list[AccountSummary]
    total: int = Field(description="Total number of matching records")
    page: int = Field(description="Current page (1-based)")
    page_size: int = Field(description="Number of items per page")
    pages: int = Field(description="Total number of pages")


# ── Error schema ──────────────────────────────────────────────────────────────


class ErrorDetail(BaseModel):
    """Standard error response envelope — never exposes internal stack traces."""

    code: str = Field(
        description="Machine-readable error code", examples=["ACCOUNT_NOT_FOUND"]
    )
    message: str = Field(description="Human-readable error description")
    correlation_id: str | None = Field(
        default=None,
        description="X-Correlation-ID from the request — include this in support tickets",
    )
