"""Authenticated, owner-scoped data exports."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, make_response, request
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from extensions import db, limiter
from models import Invoice
from utils.currency import normalize_currency_code

bp = Blueprint("exports", __name__, url_prefix="/dashboard")

_STATUS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FORMULA_PREFIXES = ("=", "+", "-", "@")

_CSV_HEADERS = (
    "Invoice Number",
    "Status",
    "Invoice Date",
    "Due Date",
    "Client Name",
    "Client Email",
    "From Company",
    "From Email",
    "Currency",
    "Subtotal",
    "Tax Rate",
    "Discount",
    "Total",
    "Amount Paid",
    "Balance Due",
    "Created At",
    "Sent At",
    "Paid At",
    "Viewed At",
    "View Count",
)


def _parse_date_filter(name: str) -> date | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        abort(400, description=f"{name} must use YYYY-MM-DD format.")


def _invoice_date(invoice: Invoice) -> date | None:
    if invoice.invoice_date_value is not None:
        return invoice.invoice_date_value
    if not invoice.invoice_date:
        return None
    try:
        return date.fromisoformat(invoice.invoice_date)
    except (TypeError, ValueError):
        return None


def _csv_text(value: object | None) -> str:
    """Prevent user-controlled text from becoming a spreadsheet formula."""
    text = "" if value is None else str(value)
    text = text.replace("\x00", "")
    first_visible = text.lstrip()[:1]
    if first_visible in _FORMULA_PREFIXES or text.startswith(("\t", "\r")):
        return f"'{text}"
    return text


def _number(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _financial_value(invoice: Invoice, decimal_name: str, legacy_name: str) -> str:
    decimal_value = getattr(invoice, decimal_name, None)
    return _number(
        decimal_value if decimal_value is not None else getattr(invoice, legacy_name)
    )


def _timestamp(value: object | None) -> str:
    return value.isoformat() if value is not None else ""


@bp.get("/invoices.csv")
@login_required
@limiter.limit("10 per minute")
def invoices_csv():
    """Download the signed-in user's invoices, optionally filtered."""
    business_today = datetime.now(
        ZoneInfo(current_app.config.get("SCHEDULER_TIMEZONE", "UTC"))
    ).date()
    query_text = request.args.get("q", "").strip()
    if len(query_text) > 200:
        abort(400, description="q must be 200 characters or fewer.")

    status = request.args.get("status", "").strip().lower()
    if status == "all":
        status = ""
    if status and not _STATUS_RE.fullmatch(status):
        abort(400, description="Invalid status filter.")

    date_from = _parse_date_filter("date_from")
    date_to = _parse_date_filter("date_to")
    if date_from and date_to and date_from > date_to:
        abort(400, description="date_from cannot be after date_to.")

    # Begin from the relationship rather than an unrestricted Invoice query so
    # ownership is part of the query even if more filters are added later.
    query = current_user.invoices
    if query_text:
        like = f"%{query_text}%"
        query = query.filter(
            db.or_(
                Invoice.invoice_number.ilike(like),
                Invoice.to_name.ilike(like),
                Invoice.to_email.ilike(like),
                Invoice.from_company.ilike(like),
            )
        )
    invoices = (
        query.options(selectinload(Invoice.payments))
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .all()
    )
    if status:
        invoices = [
            invoice
            for invoice in invoices
            if invoice.effective_status(as_of=business_today) == status
        ]
    if date_from or date_to:
        invoices = [
            invoice
            for invoice in invoices
            if (
                (invoice_date := _invoice_date(invoice)) is not None
                and (date_from is None or invoice_date >= date_from)
                and (date_to is None or invoice_date <= date_to)
            )
        ]

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(_CSV_HEADERS)
    for invoice in invoices:
        writer.writerow(
            (
                _csv_text(invoice.invoice_number),
                _csv_text(invoice.effective_status(as_of=business_today)),
                _csv_text(invoice.invoice_date),
                _csv_text(invoice.due_date),
                _csv_text(invoice.to_name),
                _csv_text(invoice.to_email),
                _csv_text(invoice.from_company),
                _csv_text(invoice.from_email),
                normalize_currency_code(invoice.currency_code),
                _financial_value(invoice, "subtotal_decimal", "subtotal"),
                _financial_value(invoice, "tax_rate_decimal", "tax_rate"),
                _financial_value(invoice, "discount_decimal", "discount"),
                _financial_value(invoice, "total_decimal", "total"),
                _number(invoice.amount_paid),
                _number(invoice.balance_due),
                _timestamp(invoice.created_at),
                _timestamp(invoice.sent_at),
                _timestamp(invoice.paid_at),
                _timestamp(invoice.viewed_at),
                invoice.view_count or 0,
            )
        )

    # A UTF-8 BOM lets Excel recognize non-ASCII client names without changing
    # the CSV's content or requiring a locale-specific encoding.
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="pdfbillr-invoices-{business_today.isoformat()}.csv"'
    )
    response.headers["Cache-Control"] = "no-store, private"
    return response
