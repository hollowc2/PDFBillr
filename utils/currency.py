"""Supported invoice currencies and centralized monetary formatting.

Currency codes are stored as invoice snapshots. The application deliberately
does not perform foreign-exchange conversion or aggregate unlike currencies.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import MappingProxyType
from typing import Any


DEFAULT_CURRENCY_CODE = "USD"

_CURRENCIES = {
    "USD": {"name": "US Dollar", "symbol": "$", "minor_units": 2},
    "CAD": {"name": "Canadian Dollar", "symbol": "CA$", "minor_units": 2},
    "EUR": {"name": "Euro", "symbol": "€", "minor_units": 2},
    "GBP": {"name": "British Pound", "symbol": "£", "minor_units": 2},
    "AUD": {"name": "Australian Dollar", "symbol": "A$", "minor_units": 2},
    "JPY": {"name": "Japanese Yen", "symbol": "¥", "minor_units": 0},
}

# Expose read-only metadata so forms and JavaScript can derive labels, symbols,
# and input precision from the same allowlist used by server-side writes.
SUPPORTED_CURRENCIES = MappingProxyType(
    {code: MappingProxyType(metadata) for code, metadata in _CURRENCIES.items()}
)


def normalize_currency_code(value: Any) -> str:
    """Return an allowlisted uppercase ISO code, defaulting safely to USD."""
    code = str(value or "").strip().upper()
    return code if code in SUPPORTED_CURRENCIES else DEFAULT_CURRENCY_CODE


def currency_minor_units(currency_code: Any) -> int:
    code = normalize_currency_code(currency_code)
    return int(SUPPORTED_CURRENCIES[code]["minor_units"])


def currency_symbol(currency_code: Any) -> str:
    code = normalize_currency_code(currency_code)
    return str(SUPPORTED_CURRENCIES[code]["symbol"])


def currency_quantum(currency_code: Any) -> Decimal:
    return Decimal(1).scaleb(-currency_minor_units(currency_code))


def currency_input_step(currency_code: Any) -> str:
    return "1" if currency_minor_units(currency_code) == 0 else "0.01"


def format_currency(value: Any, currency_code: Any = DEFAULT_CURRENCY_CODE) -> str:
    """Format a monetary value using the invoice's snapshotted currency."""
    code = normalize_currency_code(currency_code)
    metadata = SUPPORTED_CURRENCIES[code]
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    if not amount.is_finite():
        amount = Decimal("0")

    amount = amount.quantize(currency_quantum(code), rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    absolute = abs(amount)
    digits = int(metadata["minor_units"])
    rendered = f"{absolute:,.{digits}f}"
    return f"{sign}{currency_symbol(code)}{rendered}"


def currency_options() -> tuple[dict[str, Any], ...]:
    """Return serializable option metadata in the product's display order."""
    return tuple(
        {
            "code": code,
            "name": str(metadata["name"]),
            "symbol": str(metadata["symbol"]),
            "minor_units": int(metadata["minor_units"]),
            "step": currency_input_step(code),
        }
        for code, metadata in SUPPORTED_CURRENCIES.items()
    )
