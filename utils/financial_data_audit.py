"""Read-only preflight checks for the staged financial-data migration.

The audit intentionally reports only aggregate counts and database row IDs.
It never includes invoice numbers, customer details, or malformed source
values. Shadow columns remain optional until a separate backfill is approved.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import Engine, select

from models import Invoice, RecurringInvoice
from utils.helpers import MAX_ITEMS
from utils.invoice_calculations import (
    MAX_QUANTITY,
    MAX_RATE,
    MAX_TOTAL,
    InvoiceCalculationError,
    calculate_invoice,
)

MONEY_QUANTUM = Decimal("0.01")
TAX_RATE_QUANTUM = Decimal("0.0001")
MAX_TAX_RATE = Decimal("100")


@dataclass
class FinancialDataAudit:
    """Serializable, privacy-preserving result of a read-only audit."""

    invoices_scanned: int = 0
    recurring_invoices_scanned: int = 0
    blockers: dict[str, set[int]] = field(
        default_factory=lambda: defaultdict(set)
    )
    pending_backfill: dict[str, set[int]] = field(
        default_factory=lambda: defaultdict(set)
    )

    @property
    def blocker_count(self) -> int:
        return sum(len(row_ids) for row_ids in self.blockers.values())

    def as_dict(self) -> dict[str, Any]:
        def serialize(groups: dict[str, set[int]]) -> dict[str, dict[str, Any]]:
            return {
                name: {
                    "count": len(groups[name]),
                    "row_ids": sorted(groups[name]),
                }
                for name in sorted(groups)
            }

        return {
            "blocker_count": self.blocker_count,
            "blockers": serialize(self.blockers),
            "invoices_scanned": self.invoices_scanned,
            "pending_backfill": serialize(self.pending_backfill),
            "recurring_invoices_scanned": self.recurring_invoices_scanned,
        }


def _legacy_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError from None
    if parsed.isoformat() != value:
        raise ValueError
    return parsed


def _decimal(
    value: Any,
    *,
    maximum: Decimal,
    quantum: Decimal,
) -> Decimal:
    # Existing read paths interpret NULL money values as zero.
    if value is None or value == "":
        parsed = Decimal("0")
    else:
        try:
            parsed = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, AttributeError):
            raise ValueError from None
    if not parsed.is_finite() or parsed < 0 or parsed > maximum:
        raise ValueError
    try:
        return parsed.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError from None


def _parse_line_items(raw: Any) -> list[dict[str, Any]] | None:
    if raw is None or raw == "":
        return []

    def reject_nonfinite_constant(_value: str) -> None:
        raise ValueError

    try:
        parsed = json.loads(raw, parse_constant=reject_nonfinite_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, list) or len(parsed) > MAX_ITEMS:
        return None

    for item in parsed:
        if not isinstance(item, dict):
            return None
        if set(("description", "qty", "rate", "amount")) - item.keys():
            return None
        if not isinstance(item["description"], str):
            return None
        for field_name, maximum in (
            ("qty", MAX_QUANTITY),
            ("rate", MAX_RATE),
            ("amount", MAX_TOTAL),
        ):
            try:
                _decimal(
                    item[field_name],
                    maximum=maximum,
                    quantum=MONEY_QUANTUM,
                )
            except ValueError:
                return None
    return parsed


def _audit_calculation_consistency(
    result: FinancialDataAudit,
    *,
    row_id: int,
    table_prefix: str,
    line_items: list[dict[str, Any]],
    tax_rate: Any,
    discount: Any,
    currency_code: Any = "USD",
    subtotal: Any = None,
    total: Any = None,
) -> None:
    try:
        calculated = calculate_invoice(
            line_items,
            tax_rate=tax_rate,
            discount=discount,
            currency_code=currency_code,
        )
    except InvoiceCalculationError:
        result.blockers[f"{table_prefix}_unrecalculable"].add(row_id)
        return

    submitted_amounts = [
        _decimal(
            item["amount"],
            maximum=MAX_TOTAL,
            quantum=MONEY_QUANTUM,
        )
        for item in line_items
    ]
    calculated_amounts = [
        _decimal(
            item["amount"],
            maximum=MAX_TOTAL,
            quantum=MONEY_QUANTUM,
        )
        for item in calculated.line_items
    ]
    if submitted_amounts != calculated_amounts:
        result.blockers[f"{table_prefix}_line_amount_mismatch"].add(row_id)

    expected_discount = calculated.discount.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    try:
        stored_discount = _decimal(
            discount,
            maximum=MAX_TOTAL,
            quantum=MONEY_QUANTUM,
        )
    except ValueError:
        stored_discount = None
    if (
        stored_discount is not None
        and stored_discount != expected_discount
    ):
        result.blockers[
            f"{table_prefix}_discount_normalization_mismatch"
        ].add(row_id)

    if table_prefix != "invoice":
        return

    for field_name, stored_value, expected in (
        ("subtotal", subtotal, calculated.subtotal),
        ("total", total, calculated.total),
    ):
        try:
            stored = _decimal(
                stored_value,
                maximum=MAX_TOTAL,
                quantum=MONEY_QUANTUM,
            )
        except ValueError:
            continue
        expected = expected.quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if stored != expected:
            result.blockers[
                f"invoice_{field_name}_calculation_mismatch"
            ].add(row_id)


def _audit_date(
    result: FinancialDataAudit,
    *,
    row_id: int,
    legacy_value: Any,
    shadow_value: date | None,
    field_name: str,
) -> None:
    try:
        expected = _legacy_date(legacy_value)
    except ValueError:
        result.blockers[f"invoice_invalid_{field_name}"].add(row_id)
        return

    if shadow_value is None and expected is not None:
        result.pending_backfill[f"invoice_{field_name}_value"].add(row_id)
    elif shadow_value != expected:
        result.blockers[f"invoice_{field_name}_shadow_mismatch"].add(row_id)


def _audit_money(
    result: FinancialDataAudit,
    *,
    row_id: int,
    legacy_value: Any,
    shadow_value: Any,
    table_prefix: str,
    field_name: str,
    maximum: Decimal,
    quantum: Decimal,
) -> None:
    try:
        expected = _decimal(
            legacy_value,
            maximum=maximum,
            quantum=quantum,
        )
    except ValueError:
        result.blockers[f"{table_prefix}_invalid_{field_name}"].add(row_id)
        return

    if shadow_value is None:
        result.pending_backfill[f"{table_prefix}_{field_name}_decimal"].add(
            row_id
        )
        return
    try:
        actual = _decimal(
            shadow_value,
            maximum=maximum,
            quantum=quantum,
        )
    except ValueError:
        result.blockers[
            f"{table_prefix}_{field_name}_shadow_mismatch"
        ].add(row_id)
        return
    if actual != expected:
        result.blockers[
            f"{table_prefix}_{field_name}_shadow_mismatch"
        ].add(row_id)


def audit_legacy_financial_data(engine: Engine) -> FinancialDataAudit:
    """Audit legacy financial fields without writing or flushing ORM state."""
    result = FinancialDataAudit()

    invoice_columns = (
        Invoice.id,
        Invoice.currency_code,
        Invoice.invoice_date,
        Invoice.due_date,
        Invoice.line_items_json,
        Invoice.tax_rate,
        Invoice.discount,
        Invoice.subtotal,
        Invoice.total,
        Invoice.invoice_date_value,
        Invoice.due_date_value,
        Invoice.tax_rate_decimal,
        Invoice.discount_decimal,
        Invoice.subtotal_decimal,
        Invoice.total_decimal,
    )
    recurring_columns = (
        RecurringInvoice.id,
        RecurringInvoice.currency_code,
        RecurringInvoice.line_items_json,
        RecurringInvoice.tax_rate,
        RecurringInvoice.discount,
        RecurringInvoice.tax_rate_decimal,
        RecurringInvoice.discount_decimal,
    )

    # A Core connection avoids ORM autoflush. Only SELECT statements are
    # issued, and the context manager rolls back the implicit read transaction.
    with engine.connect() as connection:
        invoice_rows = connection.execute(
            select(*invoice_columns).order_by(Invoice.id)
        )
        for row in invoice_rows.mappings():
            row_id = row["id"]
            result.invoices_scanned += 1
            _audit_date(
                result,
                row_id=row_id,
                legacy_value=row["invoice_date"],
                shadow_value=row["invoice_date_value"],
                field_name="invoice_date",
            )
            _audit_date(
                result,
                row_id=row_id,
                legacy_value=row["due_date"],
                shadow_value=row["due_date_value"],
                field_name="due_date",
            )
            for field_name, maximum, quantum in (
                ("tax_rate", MAX_TAX_RATE, TAX_RATE_QUANTUM),
                ("discount", MAX_TOTAL, MONEY_QUANTUM),
                ("subtotal", MAX_TOTAL, MONEY_QUANTUM),
                ("total", MAX_TOTAL, MONEY_QUANTUM),
            ):
                _audit_money(
                    result,
                    row_id=row_id,
                    legacy_value=row[field_name],
                    shadow_value=row[f"{field_name}_decimal"],
                    table_prefix="invoice",
                    field_name=field_name,
                    maximum=maximum,
                    quantum=quantum,
                )
            line_items = _parse_line_items(row["line_items_json"])
            if line_items is None:
                result.blockers["invoice_malformed_line_items_json"].add(
                    row_id
                )
            else:
                _audit_calculation_consistency(
                    result,
                    row_id=row_id,
                    table_prefix="invoice",
                    line_items=line_items,
                    tax_rate=row["tax_rate"],
                    discount=row["discount"],
                    currency_code=row["currency_code"],
                    subtotal=row["subtotal"],
                    total=row["total"],
                )

        recurring_rows = connection.execute(
            select(*recurring_columns).order_by(RecurringInvoice.id)
        )
        for row in recurring_rows.mappings():
            row_id = row["id"]
            result.recurring_invoices_scanned += 1
            for field_name, maximum, quantum in (
                ("tax_rate", MAX_TAX_RATE, TAX_RATE_QUANTUM),
                ("discount", MAX_TOTAL, MONEY_QUANTUM),
            ):
                _audit_money(
                    result,
                    row_id=row_id,
                    legacy_value=row[field_name],
                    shadow_value=row[f"{field_name}_decimal"],
                    table_prefix="recurring_invoice",
                    field_name=field_name,
                    maximum=maximum,
                    quantum=quantum,
                )
            line_items = _parse_line_items(row["line_items_json"])
            if line_items is None:
                result.blockers[
                    "recurring_invoice_malformed_line_items_json"
                ].add(row_id)
            else:
                _audit_calculation_consistency(
                    result,
                    row_id=row_id,
                    table_prefix="recurring_invoice",
                    line_items=line_items,
                    tax_rate=row["tax_rate"],
                    discount=row["discount"],
                    currency_code=row["currency_code"],
                )

    return result
