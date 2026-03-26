import json
import re as _re
from datetime import date
from urllib.parse import quote

_HEX_RE = _re.compile(r'^#[0-9a-fA-F]{6}$')

from flask import (
    Blueprint, jsonify, make_response, render_template, request,
)
from flask_login import current_user

from extensions import db, limiter
from models import BrandingProfile, Invoice
from utils.gating import is_pro
from utils.helpers import _safe_filename
from utils.pdf import ALLOWED_THEMES, build_invoice_context, render_pdf

bp = Blueprint("public", __name__)


@bp.route("/")
def landing():
    return render_template("landing.html")


@bp.route("/app")
def index():
    return render_template("form.html", today=date.today().isoformat(), form_data=None)


@bp.route("/generate", methods=["POST"])
@limiter.limit("10 per minute; 50 per hour")
def generate():
    # Determine logo and branding for authenticated Pro users
    logo_filename = None
    accent_color  = "#1e3a8a"
    remove_footer = False

    if current_user.is_authenticated:
        branding: BrandingProfile | None = current_user.branding
        if branding and is_pro():
            logo_filename = branding.logo_filename
            raw_accent    = branding.accent_color or "#1e3a8a"
            accent_color  = raw_accent if _HEX_RE.match(raw_accent) else "#1e3a8a"
            remove_footer = branding.remove_footer

    # Determine theme (Pro only for non-default)
    theme = request.form.get("theme", "default")
    if theme not in ALLOWED_THEMES or (theme != "default" and not is_pro()):
        theme = "default"

    context = build_invoice_context(request.form, logo_filename=logo_filename, accent_color=accent_color)
    context["remove_footer"] = remove_footer

    # Validate at least one non-empty line item exists
    if not context.get("line_items"):
        from flask import flash
        flash("Please add at least one line item with a description.", "error")
        return render_template("form.html", today=date.today().isoformat(), form_data=request.form)

    # Save invoice to DB for authenticated users
    if current_user.is_authenticated:
        _save_invoice(context, theme)

    pdf_bytes = render_pdf(context, theme=theme)

    invoice_number = context["invoice_number"]
    safe_number    = _safe_filename(invoice_number)
    filename       = f"Invoice-{safe_number}.pdf"
    encoded        = quote(filename, safe="")

    action   = request.form.get("action", "download")
    disp_type = "inline" if action == "preview" else "attachment"
    content_disp = f'{disp_type}; filename="{filename}"; filename*=UTF-8\'\'{encoded}'

    response = make_response(pdf_bytes)
    response.headers["Content-Type"]        = "application/pdf"
    response.headers["Content-Disposition"] = content_disp
    response.headers["Cache-Control"]       = "no-store"
    return response


def _save_invoice(context: dict, theme: str) -> None:
    """Persist invoice to DB for the currently logged-in user."""
    from flask_login import current_user as cu
    inv = Invoice(
        user_id        = cu.id,
        invoice_number = context["invoice_number"],
        invoice_date   = context["invoice_date"],
        due_date       = context["due_date"],
        from_company   = context["from_company"],
        from_address   = context["from_address"],
        from_email     = context["from_email"],
        from_phone     = context["from_phone"],
        to_name        = context["to_name"],
        to_address     = context["to_address"],
        to_email       = context["to_email"],
        line_items_json= json.dumps(context["line_items"]),
        tax_rate       = context["tax_rate"],
        discount       = context["discount"],
        subtotal       = context["subtotal"],
        total          = context["total"],
        notes          = context["notes"],
        payment_info   = context["payment_info"],
        logo_filename  = cu.branding.logo_filename if cu.branding else None,
        theme          = theme,
        status         = "draft",
    )
    db.session.add(inv)
    db.session.commit()


@bp.route("/health")
def health():
    pdf_ok = True
    try:
        from weasyprint import HTML as _HTML  # noqa: F401
    except Exception:
        pdf_ok = False

    db_ok = True
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception:
        db_ok = False

    checks  = {"web_server": True, "pdf_engine": pdf_ok, "database": db_ok}
    overall = "ok" if all(checks.values()) else "degraded"

    if request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json":
        return jsonify({"status": overall, "checks": checks})
    return render_template("health.html", status=overall, checks=checks)
