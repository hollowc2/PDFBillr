import math
import os
import re
from datetime import date
from urllib.parse import quote

from flask import Flask, render_template, request, make_response, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from weasyprint import HTML
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# C-1: Secret key from environment variable
_secret = os.environ.get("SECRET_KEY", "")
if not _secret:
    import warnings
    warnings.warn("SECRET_KEY env var not set. Using insecure default.", stacklevel=1)
    _secret = "dev-only-insecure-default-do-not-use-in-production"
app.secret_key = _secret

# H-2: Rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",  # for multi-worker scale: use redis://
)

# H-1: Field length constants
MAX_SHORT = 200      # names, email, phone, invoice number, dates
MAX_LONG  = 2_000    # address, notes, payment_info
MAX_ITEMS = 100      # line items
MAX_DESC  = 500      # per-item description


# H-4: Safe float helper
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


# H-1: Truncate helper
def _truncate(value, max_len: int) -> str:
    if value is None:
        return ""
    return str(value)[:max_len]


# M-1: Safe filename helper
def _safe_filename(invoice_number: str) -> str:
    safe = re.sub(r'[^\w.\-]', '-', invoice_number)
    safe = re.sub(r'[-_]{2,}', '-', safe)
    safe = safe.strip('-_') or "invoice"
    return safe[:64]


# M-3: Security headers
@app.after_request
def _add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'self';"
    )
    return response


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/app")
def index():
    return render_template("form.html", today=date.today().isoformat())


@app.route("/generate", methods=["POST"])
@limiter.limit("10 per minute; 50 per hour")
def generate():
    # --- Parse form fields ---
    from_company = _truncate(request.form.get("from_company", ""), MAX_SHORT)
    from_address = _truncate(request.form.get("from_address", ""), MAX_LONG)
    from_email   = _truncate(request.form.get("from_email", ""), MAX_SHORT)
    from_phone   = _truncate(request.form.get("from_phone", ""), MAX_SHORT)

    to_name    = _truncate(request.form.get("to_name", ""), MAX_SHORT)
    to_address = _truncate(request.form.get("to_address", ""), MAX_LONG)
    to_email   = _truncate(request.form.get("to_email", ""), MAX_SHORT)

    invoice_number = _truncate(request.form.get("invoice_number", "INV-001"), MAX_SHORT)
    invoice_date   = _truncate(request.form.get("invoice_date", date.today().isoformat()), MAX_SHORT)
    due_date       = _truncate(request.form.get("due_date", ""), MAX_SHORT)  # BUG fix: was overwriting invoice_date

    notes        = _truncate(request.form.get("notes", ""), MAX_LONG)
    payment_info = _truncate(request.form.get("payment_info", ""), MAX_LONG)

    tax_rate = _safe_float(request.form.get("tax_rate", 0), min_val=0.0, max_val=100.0)
    discount = _safe_float(request.form.get("discount", 0), min_val=0.0)  # BUG fix: was overwriting tax_rate

    action = request.form.get("action", "download")

    # --- Build line items ---
    descriptions = request.form.getlist("description[]")[:MAX_ITEMS]  # H-1: cap items
    qtys         = request.form.getlist("qty[]")[:MAX_ITEMS]
    rates        = request.form.getlist("rate[]")[:MAX_ITEMS]

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
        line_items.append({
            "description": desc,
            "qty": qty,
            "rate": rate,
            "amount": amount,
        })

    tax_amount = subtotal * (tax_rate / 100)
    total = subtotal + tax_amount - discount

    context = {
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
    }

    html_string = render_template("invoice.html", **context)
    pdf_bytes = HTML(string=html_string).write_pdf()  # C-2: removed base_url to prevent SSRF

    # M-1: Safe filename with RFC 5987 Content-Disposition
    safe_number = _safe_filename(invoice_number)
    filename    = f"Invoice-{safe_number}.pdf"
    encoded     = quote(filename, safe="")

    disp_type = "inline" if action == "preview" else "attachment"
    content_disp = f'{disp_type}; filename="{filename}"; filename*=UTF-8\'\'{encoded}'

    response = make_response(pdf_bytes)
    response.headers["Content-Type"]        = "application/pdf"
    response.headers["Content-Disposition"] = content_disp
    response.headers["Cache-Control"]       = "no-store"

    # TODO: Premium — save invoice to DB, send email, associate with current_user

    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8000)
