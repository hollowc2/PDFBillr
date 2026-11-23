import re


MAX_SHORT = 200  # names, email, phone, invoice number, dates
MAX_LONG = 2_000  # address, notes, payment_info
MAX_ITEMS = 100  # line items
MAX_DESC = 500  # per-item description


def _truncate(value, max_len: int) -> str:
    if value is None:
        return ""
    return str(value)[:max_len]


def _safe_filename(invoice_number: str) -> str:
    safe = re.sub(r"[^\w.\-]", "-", invoice_number)
    safe = re.sub(r"[-_]{2,}", "-", safe)
    safe = safe.strip("-_") or "invoice"
    return safe[:64]
