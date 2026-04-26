"""Unit tests for AddressCreate / AddressUpdate / AddressResponse Pydantic schemas."""

import pytest
from pydantic import ValidationError

from src.schemas.address import AddressCreate, AddressUpdate


def _valid_payload(**overrides) -> dict:
    base = {
        "line1": "123 Main St",
        "line2": "Apt 3A",
        "city": "Boston",
        "state": "MA",
        "postal_code": "02101",
        "country": "USA",
    }
    base.update(overrides)
    return base


class TestAddressCreate:
    def test_valid_payload_accepted(self):
        addr = AddressCreate(**_valid_payload())
        assert addr.country == "USA"
        assert addr.billing_same_as_shipping is False

    def test_country_lowercased_input_uppercased(self):
        addr = AddressCreate(**_valid_payload(country="usa"))
        assert addr.country == "USA"

    def test_country_must_be_three_letters(self):
        with pytest.raises(ValidationError):
            AddressCreate(**_valid_payload(country="US"))
        with pytest.raises(ValidationError):
            AddressCreate(**_valid_payload(country="USAA"))

    def test_country_rejects_digits(self):
        with pytest.raises(ValidationError):
            AddressCreate(**_valid_payload(country="US1"))

    def test_line2_optional(self):
        addr = AddressCreate(**_valid_payload(line2=None))
        assert addr.line2 is None

    def test_line1_min_length(self):
        with pytest.raises(ValidationError):
            AddressCreate(**_valid_payload(line1=""))

    def test_control_chars_rejected_in_line1(self):
        with pytest.raises(ValidationError):
            AddressCreate(**_valid_payload(line1="123\x00Main"))

    def test_billing_same_as_shipping_accepted(self):
        addr = AddressCreate(**_valid_payload(billing_same_as_shipping=True))
        assert addr.billing_same_as_shipping is True

    def test_whitespace_stripped(self):
        addr = AddressCreate(**_valid_payload(city="  Boston  "))
        assert addr.city == "Boston"


class TestAddressUpdate:
    def test_all_fields_optional(self):
        # Empty body is structurally valid; service layer rejects it as NO_FIELDS_TO_UPDATE.
        addr = AddressUpdate()
        assert addr.line1 is None
        assert addr.country is None
        assert addr.billing_same_as_shipping is False

    def test_partial_update_with_country_normalised(self):
        addr = AddressUpdate(country="usa")
        assert addr.country == "USA"

    def test_invalid_country_rejected_on_update(self):
        with pytest.raises(ValidationError):
            AddressUpdate(country="ZZ")
