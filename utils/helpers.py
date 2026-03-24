import math
import re


MAX_SHORT = 200      # names, email, phone, invoice number, dates
MAX_LONG  = 2_000    # address, notes, payment_info
MAX_ITEMS = 100      # line items
MAX_DESC  = 500      # per-item description


def _safe_float(raw, default=0.0, min_val=None, max_val=None) -> float:
    try:
        val = float(raw or default)
    except (ValueError, TypeError):
        return default
    if not math.isfinite(val):
        return default
    if min_val is not None:
        val = max(min_val, val)
    if max_val is not None:
        val = min(max_val, val)
    return val


def _truncate(value, max_len: int) -> str:
    if value is None:
        return ""
    return str(value)[:max_len]


def _safe_filename(invoice_number: str) -> str:
    safe = re.sub(r'[^\w.\-]', '-', invoice_number)
    safe = re.sub(r'[-_]{2,}', '-', safe)
    safe = safe.strip('-_') or "invoice"
    return safe[:64]
