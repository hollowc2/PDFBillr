import json
import re as _re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

_HEX_RE = _re.compile(r'^#[0-9a-fA-F]{6}$')

from flask import (
    Blueprint, abort, current_app, flash, jsonify, make_response,
    render_template, request,
)
from flask_login import current_user
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError

from extensions import db, limiter
from models import BrandingProfile, Invoice
from utils.gating import is_pro
from utils.financial_shadow_values import (
    FinancialShadowValueError,
    invoice_shadow_values,
)
from utils.helpers import _safe_filename
from utils.invoice_calculations import InvoiceCalculationError
from utils.invoice_numbers import invoice_number_exists
from utils.pdf import ALLOWED_THEMES, build_invoice_context, context_from_invoice, render_pdf
from utils.validation import PaymentURLValidationError

bp = Blueprint("public", __name__)


@bp.route("/")
def landing():
    return render_template("landing.html")


@bp.route("/app")
def index():
    selected_client_id = request.args.get("client_id")
    return _render_invoice_form(
        None,
        selected_client_id=selected_client_id,
        selected_service_id=request.args.get("service_id"),
    )


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

    selected_client_id = None
    if current_user.is_authenticated:
        from blueprints.clients import selected_owned_client_id

        selected_client_id = selected_owned_client_id(
            request.form.get("client_id")
        )

    try:
        context = build_invoice_context(
            request.form,
            logo_filename=logo_filename,
            accent_color=accent_color,
        )
    except (InvoiceCalculationError, PaymentURLValidationError) as exc:
        flash(str(exc), "error")
        return _render_invoice_form(request.form)
    context["remove_footer"] = remove_footer

    # Validate at least one non-empty line item exists
    if not context.get("line_items"):
        flash("Please add at least one line item with a description.", "error")
        return _render_invoice_form(request.form)

    # Reject known number conflicts before the expensive PDF render. The
    # database constraint still closes a concurrent race in _save_invoice.
    if (
        current_user.is_authenticated
        and invoice_number_exists(current_user.id, context["invoice_number"])
    ):
        flash(
            "That invoice number is already in use. Choose a different number.",
            "error",
        )
        return _render_invoice_form(request.form)

    shadow_values = None
    if current_user.is_authenticated:
        try:
            shadow_values = invoice_shadow_values(
                invoice_date=context["invoice_date"],
                due_date=context["due_date"],
                tax_rate=context["tax_rate"],
                discount=context["discount"],
                subtotal=context["subtotal"],
                total=context["total"],
            )
        except FinancialShadowValueError as exc:
            flash(str(exc), "error")
            return _render_invoice_form(request.form)

    pdf_bytes = render_pdf(context, theme=theme)

    # Persist only after rendering succeeds so a PDF failure cannot leave an
    # unexpected saved invoice behind.
    if (
        current_user.is_authenticated
        and not _save_invoice(
            context,
            theme,
            shadow_values,
            client_id=selected_client_id,
        )
    ):
        flash(
            "That invoice number was just used by another request. "
            "Choose a different number.",
            "error",
        )
        return _render_invoice_form(request.form)

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


def _save_invoice(
    context: dict,
    theme: str,
    shadow_values: dict,
    *,
    client_id: int | None = None,
) -> bool:
    """Persist an invoice, returning false for a same-user number conflict."""
    from flask_login import current_user as cu

    invoice_number = context["invoice_number"]
    if invoice_number_exists(cu.id, invoice_number):
        return False
    if client_id is None:
        from blueprints.clients import selected_owned_client_id

        client_id = selected_owned_client_id(request.form.get("client_id"))

    inv = Invoice(
        user_id        = cu.id,
        client_id      = client_id,
        invoice_number = invoice_number,
        currency_code  = context["currency_code"],
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
        invoice_date_value=shadow_values["invoice_date_value"],
        due_date_value=shadow_values["due_date_value"],
        tax_rate_decimal=shadow_values["tax_rate_decimal"],
        discount_decimal=shadow_values["discount_decimal"],
        subtotal_decimal=shadow_values["subtotal_decimal"],
        total_decimal=shadow_values["total_decimal"],
        notes          = context["notes"],
        payment_info   = context["payment_info"],
        payment_url    = context["payment_url"],
        logo_filename  = (
            cu.branding.logo_filename
            if cu.branding and is_pro(cu)
            else None
        ),
        theme          = theme,
        status         = "draft",
        view_token     = secrets.token_urlsafe(32),
    )
    db.session.add(inv)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Close the race between the friendly pre-check and the database
        # uniqueness constraint without hiding unrelated integrity failures.
        if invoice_number_exists(cu.id, invoice_number):
            return False
        raise
    return True


def _render_invoice_form(
    form_data,
    *,
    selected_client_id=None,
    selected_service_id=None,
):
    from blueprints.clients import invoice_form_context

    return render_template(
        "form.html",
        **invoice_form_context(
            form_data,
            selected_client_id=selected_client_id,
            selected_service_id=selected_service_id,
        ),
    )


@bp.route("/invoice/view/<token>")
@limiter.limit("60 per minute")
def invoice_view(token: str):
    """Public invoice view link — records when a client opens the invoice."""
    inv = Invoice.query.filter_by(view_token=token).first_or_404()
    update_result = db.session.execute(
        update(Invoice)
        .where(Invoice.id == inv.id, Invoice.view_token == token)
        .values(
            viewed_at=func.coalesce(Invoice.viewed_at, datetime.now(timezone.utc)),
            view_count=func.coalesce(Invoice.view_count, 0) + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if update_result.rowcount != 1:
        db.session.rollback()
        abort(404)
    db.session.commit()
    db.session.refresh(inv)
    context = context_from_invoice(inv)
    business_today = datetime.now(
        ZoneInfo(current_app.config.get("SCHEDULER_TIMEZONE", "UTC"))
    ).date()
    return render_template(
        "invoice_view.html",
        invoice=inv,
        lifecycle_status=inv.effective_status(as_of=business_today),
        **context,
    )


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
        db.session.rollback()
        db_ok = False

    limiter_ok = True
    limiter_storage_uri = str(
        current_app.config.get("RATELIMIT_STORAGE_URI", "memory://")
    ).lower()
    if not limiter_storage_uri.startswith("memory://"):
        try:
            limiter_ok = bool(limiter.storage.check())
        except Exception:
            limiter_ok = False

    checks = {
        "web_server": True,
        "pdf_engine": pdf_ok,
        "database": db_ok,
        "rate_limiter": limiter_ok,
    }
    overall = "ok" if all(checks.values()) else "degraded"
    status_code = 200 if overall == "ok" else 503

    if request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json":
        return jsonify({"status": overall, "checks": checks}), status_code
    return (
        render_template("health.html", status=overall, checks=checks),
        status_code,
    )


@bp.route("/health/live")
def liveness():
    """Process-only liveness check; does not probe external dependencies."""
    return jsonify({"status": "ok"}), 200
