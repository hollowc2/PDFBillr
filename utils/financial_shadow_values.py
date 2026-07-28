"""Explicit conversion helpers for transitional financial shadow columns.

Legacy float and string columns remain the application's read source during the
staged migration.  New ORM writes call these helpers after normal validation so
the typed shadow columns receive deterministic Decimal and date values without
introducing hidden model events.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from utils.invoice_calculations import MAX_TAX_RATE, MAX_TOTAL

MONEY_QUANTUM = Decimal("0.01")
TAX_RATE_QUANTUM = Decimal("0.0001")


class FinancialShadowValueError(ValueError):
    """Raised when a supposedly validated value cannot populate a shadow column."""


def invoice_shadow_values(
    *,
    invoice_date: str | date | None,
    due_date: str | date | None,
    tax_rate: Any,
    discount: Any,
    subtotal: Any,
    total: Any,
) -> dict[str, date | Decimal | None]:
    """Return typed values for an Invoice's transitional shadow columns."""
    return {
        "invoice_date_value": _parse_date(invoice_date, "Invoice date"),
        "due_date_value": _parse_date(due_date, "Due date"),
        "tax_rate_decimal": _quantize_decimal(
            tax_rate,
            TAX_RATE_QUANTUM,
            "Tax rate",
            maximum=MAX_TAX_RATE,
        ),
        "discount_decimal": _quantize_decimal(
            discount,
            MONEY_QUANTUM,
            "Discount",
            maximum=MAX_TOTAL,
        ),
        "subtotal_decimal": _quantize_decimal(
            subtotal,
            MONEY_QUANTUM,
            "Subtotal",
            maximum=MAX_TOTAL,
        ),
        "total_decimal": _quantize_decimal(
            total,
            MONEY_QUANTUM,
            "Total",
            maximum=MAX_TOTAL,
        ),
    }


def recurring_invoice_shadow_values(
    *,
    tax_rate: Any,
    discount: Any,
) -> dict[str, Decimal]:
    """Return typed values for a RecurringInvoice's financial shadows."""
    return {
        "tax_rate_decimal": _quantize_decimal(
            tax_rate,
            TAX_RATE_QUANTUM,
            "Tax rate",
            maximum=MAX_TAX_RATE,
        ),
        "discount_decimal": _quantize_decimal(
            discount,
            MONEY_QUANTUM,
            "Discount",
            maximum=MAX_TOTAL,
        ),
    }


def _parse_date(value: str | date | None, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise FinancialShadowValueError(
            f"{field_name} must be a valid date in YYYY-MM-DD format."
        ) from None
    if parsed.isoformat() != value:
        raise FinancialShadowValueError(
            f"{field_name} must be a valid date in YYYY-MM-DD format."
        )
    return parsed


def _quantize_decimal(
    value: Any,
    quantum: Decimal,
    field_name: str,
    *,
    maximum: Decimal,
) -> Decimal:
    if isinstance(value, bool):
        raise FinancialShadowValueError(f"{field_name} must be a number.")
    if value is None or value == "":
        value = 0
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise FinancialShadowValueError(
            f"{field_name} must be a valid number."
        ) from None
    if not decimal_value.is_finite():
        raise FinancialShadowValueError(f"{field_name} must be finite.")
    if decimal_value < 0:
        raise FinancialShadowValueError(f"{field_name} cannot be negative.")
    if decimal_value > maximum:
        raise FinancialShadowValueError(f"{field_name} is too large.")
    try:
        return decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise FinancialShadowValueError(
            f"{field_name} cannot be represented safely."
        ) from None
