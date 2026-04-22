"""
Unit tests for Pydantic input validation schemas.

These tests verify that the OWASP A03 injection-prevention rules in
AccountCreate and AccountUpdate reject malformed or dangerous input.
No database or external services are required.
"""

import pytest
from pydantic import ValidationError

from src.schemas.account import AccountCreate, AccountUpdate
from src.models.account import AccountStatus


# ── AccountCreate ─────────────────────────────────────────────────────────────


class TestAccountCreate:
    def _valid(self, **overrides) -> dict:
        base = {
            "customer_number": "CUST-001",
            "first_name": "Jane",
            "last_name": "Smith",
            "email": "jane.smith@example.com",
            "phone": "+1-800-555-0100",
        }
        return {**base, **overrides}

    def test_valid_payload_accepted(self):
        account = AccountCreate(**self._valid())
        assert account.customer_number == "CUST-001"
        assert account.email == "jane.smith@example.com"

    def test_customer_number_uppercased(self):
        account = AccountCreate(**self._valid(customer_number="cust-001"))
        assert account.customer_number == "CUST-001"

    def test_customer_number_rejects_special_chars(self):
        with pytest.raises(ValidationError, match="customer_number"):
            AccountCreate(
                **self._valid(customer_number="CUST 001; DROP TABLE accounts--")
            )

    def test_customer_number_too_short(self):
        with pytest.raises(ValidationError):
            AccountCreate(**self._valid(customer_number="AB"))

    def test_email_invalid_format_rejected(self):
        with pytest.raises(ValidationError):
            AccountCreate(**self._valid(email="not-an-email"))

    def test_email_with_injection_rejected(self):
        with pytest.raises(ValidationError):
            AccountCreate(**self._valid(email="x@x.com<script>alert(1)</script>"))

    def test_phone_valid_formats(self):
        for phone in ["+44 20 7946 0958", "1-800-555-0100", "(800) 555.0100"]:
            account = AccountCreate(**self._valid(phone=phone))
            assert account.phone == phone

    def test_phone_rejects_invalid_chars(self):
        with pytest.raises(ValidationError, match="phone"):
            AccountCreate(**self._valid(phone="<script>alert(1)</script>"))

    def test_phone_optional(self):
        payload = self._valid()
        del payload["phone"]
        account = AccountCreate(**payload)
        assert account.phone is None

    def test_first_name_control_char_rejected(self):
        with pytest.raises(ValidationError):
            AccountCreate(**self._valid(first_name="Jane\x00"))

    def test_first_name_too_long_rejected(self):
        with pytest.raises(ValidationError):
            AccountCreate(**self._valid(first_name="A" * 101))

    def test_whitespace_stripped(self):
        account = AccountCreate(**self._valid(first_name="  Jane  "))
        assert account.first_name == "Jane"


# ── AccountUpdate ─────────────────────────────────────────────────────────────


class TestAccountUpdate:
    def test_all_fields_optional(self):
        update = AccountUpdate()
        assert update.first_name is None
        assert update.email is None
        assert update.status is None

    def test_partial_update_only_email(self):
        update = AccountUpdate(email="new@example.com")
        dumped = update.model_dump(exclude_none=True)
        assert list(dumped.keys()) == ["email"]

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            AccountUpdate(status="deleted")

    def test_valid_status_values(self):
        for s in [
            AccountStatus.ACTIVE,
            AccountStatus.INACTIVE,
            AccountStatus.SUSPENDED,
        ]:
            update = AccountUpdate(status=s)
            assert update.status == s

    def test_phone_rejects_invalid_chars(self):
        with pytest.raises(ValidationError, match="phone"):
            AccountUpdate(phone="'; DROP TABLE--")
