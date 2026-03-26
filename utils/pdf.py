import base64
import json
import mimetypes
import os
import re as _re

from flask import current_app, render_template
from weasyprint import HTML

_HEX_RE = _re.compile(r'^#[0-9a-fA-F]{6}$')

from utils.helpers import (
    MAX_DESC, MAX_ITEMS, MAX_LONG, MAX_SHORT,
    _safe_float, _truncate,
)

ALLOWED_THEMES = {"default", "minimal", "corporate", "creative"}

_THEME_TEMPLATES = {
    "default":   "invoice.html",
    "minimal":   "invoice_minimal.html",
    "corporate": "invoice_corporate.html",
    "creative":  "invoice_creative.html",
}


def _logo_data_uri(filename: str) -> str | None:
    """Read a logo file from static/logos/ and return a data: URI for WeasyPrint."""
    if not filename:
        return None
    logos_dir = os.path.join(current_app.root_path, "static", "logos")
    # Prevent path traversal: only allow basename
    safe_name = os.path.basename(filename)
    path = os.path.join(logos_dir, safe_name)
    if not os.path.isfile(path):
        return None
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{data}"


def build_invoice_context(form, logo_filename=None, accent_color="#1e3a8a"):
    """Parse a Flask request.form and return the full template context dict."""
    from_company = _truncate(form.get("from_company", ""), MAX_SHORT)
    from_address = _truncate(form.get("from_address", ""), MAX_LONG)
    from_email   = _truncate(form.get("from_email", ""), MAX_SHORT)
    from_phone   = _truncate(form.get("from_phone", ""), MAX_SHORT)

    to_name    = _truncate(form.get("to_name", ""), MAX_SHORT)
    to_address = _truncate(form.get("to_address", ""), MAX_LONG)
    to_email   = _truncate(form.get("to_email", ""), MAX_SHORT)

    from datetime import date as _date
    invoice_number = _truncate(form.get("invoice_number", "INV-001"), MAX_SHORT)
    invoice_date   = _truncate(form.get("invoice_date", _date.today().isoformat()), MAX_SHORT)
    due_date       = _truncate(form.get("due_date", ""), MAX_SHORT)

    notes        = _truncate(form.get("notes", ""), MAX_LONG)
    payment_info = _truncate(form.get("payment_info", ""), MAX_LONG)

    tax_rate = _safe_float(form.get("tax_rate", 0), min_val=0.0, max_val=100.0)
    discount = _safe_float(form.get("discount", 0), min_val=0.0)

    descriptions = form.getlist("description[]")[:MAX_ITEMS]
    qtys         = form.getlist("qty[]")[:MAX_ITEMS]
    rates        = form.getlist("rate[]")[:MAX_ITEMS]

    line_items = []
    subtotal = 0.0
    for desc, qty_str, rate_str in zip(descriptions, qtys, rates):
        desc = _truncate(desc, MAX_DESC)
        if not desc.strip():
            continue
        qty    = _safe_float(qty_str, min_val=0.0)
        rate   = _safe_float(rate_str, min_val=0.0)
        amount = qty * rate
        subtotal += amount
        line_items.append({"description": desc, "qty": qty, "rate": rate, "amount": amount})

    tax_amount = subtotal * (tax_rate / 100)
    discount   = min(discount, subtotal + tax_amount)
    total      = subtotal + tax_amount - discount

    logo_url = _logo_data_uri(logo_filename) if logo_filename else None

    return {
        "invoice_number": invoice_number,
        "invoice_date":   invoice_date,
        "due_date":       due_date,
        "from_company":   from_company,
        "from_address":   from_address,
        "from_email":     from_email,
        "from_phone":     from_phone,
        "to_name":        to_name,
        "to_address":     to_address,
        "to_email":       to_email,
        "line_items":     line_items,
        "tax_rate":       tax_rate,
        "tax_amount":     tax_amount,
        "discount":       discount,
        "subtotal":       subtotal,
        "total":          total,
        "notes":          notes,
        "payment_info":   payment_info,
        "logo_url":       logo_url,
        "accent_color":   accent_color,
    }


def context_from_invoice(invoice) -> dict:
    """Reconstruct template context from a saved Invoice model instance."""
    line_items = json.loads(invoice.line_items_json or "[]")
    tax_amount = invoice.subtotal * (invoice.tax_rate / 100) if invoice.tax_rate else 0.0
    logo_url   = _logo_data_uri(invoice.logo_filename) if invoice.logo_filename else None

    # Pull branding profile fields if available
    accent_color = "#1e3a8a"
    remove_footer = False
    if invoice.user and invoice.user.branding:
        raw_accent = invoice.user.branding.accent_color or "#1e3a8a"
        accent_color = raw_accent if _HEX_RE.match(raw_accent) else "#1e3a8a"
        remove_footer = bool(invoice.user.branding.remove_footer)

    return {
        "invoice_number": invoice.invoice_number,
        "invoice_date":   invoice.invoice_date,
        "due_date":       invoice.due_date,
        "from_company":   invoice.from_company,
        "from_address":   invoice.from_address,
        "from_email":     invoice.from_email,
        "from_phone":     invoice.from_phone,
        "to_name":        invoice.to_name,
        "to_address":     invoice.to_address,
        "to_email":       invoice.to_email,
        "line_items":     line_items,
        "tax_rate":       invoice.tax_rate,
        "tax_amount":     tax_amount,
        "discount":       invoice.discount,
        "subtotal":       invoice.subtotal,
        "total":          invoice.total,
        "notes":          invoice.notes,
        "payment_info":   invoice.payment_info,
        "logo_url":       logo_url,
        "accent_color":   accent_color,
        "remove_footer":  remove_footer,
    }


def render_pdf(context: dict, theme: str = "default") -> bytes:
    template_name = _THEME_TEMPLATES.get(theme, "invoice.html")
    html_string = render_template(template_name, **context)
    return HTML(string=html_string).write_pdf()
