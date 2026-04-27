"""
Country-specific address rules.

Validation and dedup behaviour vary by country (postal-code format, etc.).
Add a new country by appending an entry to ``COUNTRY_RULES``; unsupported
countries fail loud with ``UnsupportedCountryError`` so we never silently
apply a wrong default.

Country codes are ISO 3166-1 alpha-3.
"""

import re
from dataclasses import dataclass
from typing import Pattern


@dataclass(frozen=True)
class CountryRule:
    """Per-country address validation + dedup config."""

    postal_code_dedup_length: int
    postal_code_pattern: Pattern[str]


COUNTRY_RULES: dict[str, CountryRule] = {
    "USA": CountryRule(
        postal_code_dedup_length=5,
        postal_code_pattern=re.compile(r"^\d{5}(-\d{4})?$"),
    ),
    # Add GBR, IND, etc. as they are productionised.
}


class UnsupportedCountryError(ValueError):
    """Raised when an address payload references a country we don't yet support."""

    def __init__(self, country: str) -> None:
        self.country = country
        super().__init__(f"Country '{country}' is not yet supported")


def get_country_rule(country: str) -> CountryRule:
    """Return the rule for ``country`` (alpha-3, case-insensitive). Raises if unknown."""
    rule = COUNTRY_RULES.get(country.upper())
    if rule is None:
        raise UnsupportedCountryError(country)
    return rule
