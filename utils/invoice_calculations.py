"""Authoritative invoice calculation and numeric validation.

The database currently stores floats for backward compatibility. All parsing,
validation, and arithmetic happens with Decimal before values cross that legacy
storage boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

from utils.currency import currency_quantum, normalize_currency_code
from utils.helpers import MAX_DESC, MAX_ITEMS, _truncate

CENT = Decimal("0.01")
MAX_QUANTITY = Decimal("1000000")
MAX_RATE = Decimal("100000000")
MAX_TAX_RATE = Decimal("100")
MAX_TOTAL = Decimal("1000000000000")


class InvoiceCalculationError(ValueError):
    """Raised when invoice numeric input cannot produce a safe document."""


@dataclass(frozen=True)
class InvoiceCalculation:
    line_items: list[dict[str, Any]]
    tax_rate: Decimal
    tax_amount: Decimal
    discount: Decimal
    subtotal: Decimal
    total: Decimal

    def template_values(self) -> dict[str, Any]:
        """Return legacy-compatible JSON/template/database scalar values."""
        return {
            "line_items": self.line_items,
            "tax_rate": float(self.tax_rate),
            "tax_amount": float(self.tax_amount),
            "discount": float(self.discount),
            "subtotal": float(self.subtotal),
            "total": float(self.total),
        }


def _parse_decimal(
    raw: Any,
    field_name: str,
    *,
    maximum: Decimal,
    default: Decimal = Decimal("0"),
) -> Decimal:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        raise InvoiceCalculationError(f"{field_name} must be a number.")
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise InvoiceCalculationError(f"{field_name} must be a valid number.") from None
    if not value.is_finite():
        raise InvoiceCalculationError(f"{field_name} must be finite.")
    if value < 0:
        raise InvoiceCalculationError(f"{field_name} cannot be negative.")
    if value > maximum:
        raise InvoiceCalculationError(f"{field_name} is too large.")
    return value


def quantize_money(value: Any) -> Decimal:
    """Convert a trusted scalar to a two-decimal monetary Decimal."""
    try:
        decimal_value = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        decimal_value = Decimal("0")
    if not decimal_value.is_finite():
        return Decimal("0")
    try:
        return decimal_value.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return Decimal("0")


def calculate_tax_amount(
    subtotal: Any,
    tax_rate: Any,
    *,
    currency_code: Any = "USD",
) -> float:
    quantum = currency_quantum(currency_code)
    subtotal_decimal = quantize_money(subtotal).quantize(
        quantum, rounding=ROUND_HALF_UP
    )
    try:
        rate_decimal = Decimal(str(tax_rate or 0))
    except (InvalidOperation, ValueError):
        rate_decimal = Decimal("0")
    if not rate_decimal.is_finite():
        rate_decimal = Decimal("0")
    return float(
        (subtotal_decimal * rate_decimal / Decimal("100")).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    )


def calculate_invoice(
    raw_items: Iterable[Mapping[str, Any]] | Any,
    *,
    tax_rate: Any = 0,
    discount: Any = 0,
    currency_code: Any = "USD",
) -> InvoiceCalculation:
    """Validate line items and calculate a canonical invoice total.

    Each line amount is rounded to cents with ROUND_HALF_UP before subtotaling.
    Tax is rounded to cents from that subtotal. The fixed discount is rounded to
    cents and capped at subtotal plus tax, so total can never be negative.
    """
    if not isinstance(raw_items, (list, tuple)):
        raise InvoiceCalculationError("Line items must be a list.")
    if len(raw_items) > MAX_ITEMS:
        raise InvoiceCalculationError(f"At most {MAX_ITEMS} line items are allowed.")

    normalized_currency_code = normalize_currency_code(currency_code)
    quantum = currency_quantum(normalized_currency_code)
    normalized_items: list[dict[str, Any]] = []
    subtotal = Decimal("0")

    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, Mapping):
            raise InvoiceCalculationError(f"Line item {index} must be an object.")

        description = _truncate(raw_item.get("description", ""), MAX_DESC)
        if not description.strip():
            continue

        quantity = _parse_decimal(
            raw_item.get("qty"),
            f"Line item {index} quantity",
            maximum=MAX_QUANTITY,
        )
        rate = _parse_decimal(
            raw_item.get("rate"),
            f"Line item {index} rate",
            maximum=MAX_RATE,
        )
        amount = (quantity * rate).quantize(quantum, rounding=ROUND_HALF_UP)
        subtotal += amount
        if subtotal > MAX_TOTAL:
            raise InvoiceCalculationError("Invoice subtotal is too large.")

        normalized_items.append(
            {
                "description": description,
                "qty": float(quantity),
                "rate": float(rate),
                "amount": float(amount),
            }
        )

    normalized_tax_rate = _parse_decimal(
        tax_rate,
        "Tax rate",
        maximum=MAX_TAX_RATE,
    )
    tax_amount = (subtotal * normalized_tax_rate / Decimal("100")).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )
    normalized_discount = _parse_decimal(
        discount,
        "Discount",
        maximum=MAX_TOTAL,
    ).quantize(quantum, rounding=ROUND_HALF_UP)
    balance = subtotal + tax_amount
    if balance > MAX_TOTAL:
        raise InvoiceCalculationError("Invoice total is too large.")
    normalized_discount = min(normalized_discount, balance)
    total = balance - normalized_discount

    return InvoiceCalculation(
        line_items=normalized_items,
        tax_rate=normalized_tax_rate,
        tax_amount=tax_amount,
        discount=normalized_discount,
        subtotal=subtotal,
        total=total,
    )
