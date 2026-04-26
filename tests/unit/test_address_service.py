"""Unit tests for address_service.

Pure helper functions (build_dedup_key) are tested directly. Higher-level service
methods are exercised in tests/integration/ against a real database — the dedup
partial unique index, FK cycles, and txn semantics are too DB-specific to mock
faithfully.
"""

import pytest

from src.country_rules import UnsupportedCountryError
from src.services.address_service import build_dedup_key


class TestBuildDedupKey:
    def test_basic_usa_address(self):
        key = build_dedup_key(
            line1="123 Main St",
            line2="Apt 3A",
            city="Boston",
            state="MA",
            postal_code="02101",
            country="USA",
        )
        assert key == "123 m|apt 3|boston|ma|02101"

    def test_lowercased_and_stripped(self):
        key = build_dedup_key(
            line1="  123 MAIN st  ",
            line2="  APT 3A  ",
            city="  BOSTON  ",
            state="MA",
            postal_code="02101",
            country="USA",
        )
        assert key == "123 m|apt 3|boston|ma|02101"

    def test_null_line2_becomes_empty_segment(self):
        key = build_dedup_key(
            line1="123 Main St",
            line2=None,
            city="Boston",
            state="MA",
            postal_code="02101",
            country="USA",
        )
        # Empty segment between "123 m" and "boston"
        assert key == "123 m||boston|ma|02101"

    def test_postal_code_truncated_to_first_5(self):
        key = build_dedup_key(
            line1="123 Main St",
            line2=None,
            city="Boston",
            state="MA",
            postal_code="02101-1234",
            country="USA",
        )
        assert key.endswith("|02101")

    def test_postal_code_shorter_than_5_kept_asis(self):
        # USA's rule asks for 5 chars; if input has fewer, use what's there.
        key = build_dedup_key(
            line1="123 Main St",
            line2=None,
            city="Boston",
            state="MA",
            postal_code="021",
            country="USA",
        )
        assert key.endswith("|021")

    def test_apt_3_and_apt_5_produce_distinct_keys(self):
        # Scenario B trade-off: first 5 chars of line2 distinguishes Apt 3 vs Apt 5.
        a = build_dedup_key("123 Main", "Apt 3", "Boston", "MA", "02101", "USA")
        b = build_dedup_key("123 Main", "Apt 5", "Boston", "MA", "02101", "USA")
        assert a != b

    def test_apt_3a_and_3b_collapse_to_same_key(self):
        # Documented Scenario B limitation — first 5 chars of "Apt 3A" and "Apt 3B"
        # both yield "apt 3"; user accepted this when picking Scenario B.
        a = build_dedup_key("123 Main", "Apt 3A", "Boston", "MA", "02101", "USA")
        b = build_dedup_key("123 Main", "Apt 3B", "Boston", "MA", "02101", "USA")
        assert a == b

    def test_unsupported_country_raises(self):
        with pytest.raises(UnsupportedCountryError):
            build_dedup_key("123 Main", None, "London", "X", "SW1", "GBR")
