"""Shared writes for validated invoice contexts."""

import json


def apply_invoice_fields(inv, context: dict, shadow_values: dict) -> None:
    for name in (
        "invoice_number",
        "currency_code",
        "page_size",
        "invoice_date",
        "due_date",
        "from_company",
        "from_address",
        "from_email",
        "from_phone",
        "to_name",
        "to_address",
        "to_email",
        "tax_rate",
        "discount",
        "subtotal",
        "total",
        "notes",
        "payment_info",
        "payment_url",
    ):
        setattr(inv, name, context[name])
    inv.line_items_json = json.dumps(context["line_items"])
    for name in (
        "invoice_date_value",
        "due_date_value",
        "tax_rate_decimal",
        "discount_decimal",
        "subtotal_decimal",
        "total_decimal",
    ):
        setattr(inv, name, shadow_values[name])
