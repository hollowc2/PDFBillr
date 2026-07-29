import json
import math
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import (
    Blueprint, abort, current_app, flash, make_response,
    redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user, login_required
from flask_mail import Message
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload
from werkzeug.datastructures import MultiDict

from extensions import db, limiter, mail
from models import (
    BrandingProfile,
    Invoice,
    InvoiceDelivery,
    InvoicePayment,
    RecurringInvoice,
    ReminderPreference,
)
from utils.currency import (
    currency_input_step,
    format_currency,
    normalize_currency_code,
)
from utils.financial_shadow_values import (
    FinancialShadowValueError,
    invoice_shadow_values,
    recurring_invoice_shadow_values,
)
from utils.gating import is_pro, pro_required
from utils.helpers import _safe_filename
from utils.invoice_calculations import InvoiceCalculationError, calculate_invoice
from utils.invoice_numbers import invoice_number_exists, next_available_invoice_number
from utils.pdf import (
    ALLOWED_THEMES,
    build_invoice_context,
    context_from_invoice,
    render_pdf,
)
from utils.uploads import (
    LogoValidationError,
    delete_user_logo,
    resolve_logo_path,
    store_logo,
)
from utils.urls import external_url
from utils.validation import (
    PaymentURLValidationError,
    is_valid_email,
    normalize_email,
    normalize_payment_url,
)

_VALID_INTERVALS = {"weekly", "biweekly", "monthly", "quarterly"}
_PAYMENT_METHODS = {
    "bank_transfer",
    "cash",
    "card",
    "check",
    "manual",
    "other",
}

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

_PER_PAGE = 20
_DASHBOARD_STATUSES = {
    "all",
    "draft",
    "sent",
    "finalized",
    "partial",
    "overdue",
    "paid",
    "void",
}
_DASHBOARD_SORTS = {
    "newest",
    "oldest",
    "due_soonest",
    "amount_high",
    "amount_low",
}


# ---------------------------------------------------------------------------
# Invoice list
# ---------------------------------------------------------------------------

@bp.route("/")
@login_required
def index():
    page = max(request.args.get("page", 1, type=int), 1)
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "all").strip().lower()
    sort = request.args.get("sort", "newest").strip().lower()
    due_from_raw = request.args.get("due_from", "").strip()
    due_to_raw = request.args.get("due_to", "").strip()
    if status not in _DASHBOARD_STATUSES:
        status = "all"
    if sort not in _DASHBOARD_SORTS:
        sort = "newest"

    all_invoices = current_user.invoices.options(
        selectinload(Invoice.payments)
    ).all()
    invoices = all_invoices
    if q:
        query_text = q.casefold()
        invoices = [
            invoice
            for invoice in invoices
            if query_text in (invoice.invoice_number or "").casefold()
            or query_text in (invoice.to_name or "").casefold()
        ]
    today = _dashboard_today()
    metrics = _receivables_metrics(all_invoices, today=today)
    due_from = _parse_query_date(due_from_raw)
    due_to = _parse_query_date(due_to_raw)

    if status != "all":
        invoices = [
            invoice
            for invoice in invoices
            if invoice.effective_status(as_of=today) == status
        ]
    if due_from is not None:
        invoices = [
            invoice
            for invoice in invoices
            if invoice.due_date_as_date is not None
            and invoice.due_date_as_date >= due_from
        ]
    if due_to is not None:
        invoices = [
            invoice
            for invoice in invoices
            if invoice.due_date_as_date is not None
            and invoice.due_date_as_date <= due_to
        ]

    invoices = _sort_dashboard_invoices(invoices, sort)
    pagination = _paginate_items(invoices, page=page, per_page=_PER_PAGE)
    filters = {
        "q": q,
        "status": status,
        "sort": sort,
        "due_from": due_from_raw,
        "due_to": due_to_raw,
    }
    return render_template(
        "dashboard/index.html",
        pagination=pagination,
        q=q,
        filters=filters,
        metrics=metrics,
        dashboard_today=today,
    )


# ---------------------------------------------------------------------------
# Invoice detail
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>")
@login_required
def invoice_detail(invoice_id: int):
    inv = _own_invoice(invoice_id)
    public_view_url = (
        external_url("public.invoice_view", token=inv.view_token)
        if inv.view_token
        else None
    )
    return render_template(
        "dashboard/invoice_detail.html",
        invoice=inv,
        public_view_url=public_view_url,
        dashboard_today=_dashboard_today(),
        payment_dates={
            payment.id: _payment_business_date(
                payment.paid_at,
                current_app.config.get("SCHEDULER_TIMEZONE", "UTC"),
            ).isoformat()
            for payment in inv.payments
            if payment.paid_at is not None
        },
    )


@bp.route("/invoice/<int:invoice_id>/reminders", methods=["POST"])
@login_required
@pro_required
def invoice_reminders(invoice_id: int):
    inv = _own_invoice(invoice_id)
    inv.payment_reminders_enabled = request.form.get("enabled") == "1"
    db.session.commit()
    state = "enabled" if inv.payment_reminders_enabled else "disabled"
    flash(f"Payment reminders {state} for this invoice.", "success")
    return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))


# ---------------------------------------------------------------------------
# Payment lifecycle
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>/payments", methods=["POST"])
@login_required
def invoice_record_payment(invoice_id: int):
    inv = _own_invoice_for_update(invoice_id)
    if inv.status == "void":
        flash("A void invoice cannot accept payments.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))
    if inv.status == "paid":
        flash("This invoice is already paid.", "info")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    try:
        amount = _payment_amount(
            request.form.get("amount"),
            currency_code=inv.currency_code,
        )
        paid_at = _payment_datetime(request.form.get("payment_date"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    if amount > inv.balance_due:
        flash(
            f"Payment cannot exceed the outstanding balance of "
            f"{format_currency(inv.balance_due, inv.currency_code)}.",
            "error",
        )
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    method = (request.form.get("method") or "other").strip().lower()
    if method not in _PAYMENT_METHODS:
        flash("Select a valid payment method.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    payment = InvoicePayment(
        invoice=inv,
        amount=amount,
        paid_at=paid_at,
        method=method,
        reference=(request.form.get("reference") or "").strip()[:200] or None,
        note=(request.form.get("note") or "").strip()[:2000] or None,
    )
    db.session.add(payment)
    db.session.flush()
    inv.sync_payment_status(changed_at=paid_at)
    db.session.commit()

    if inv.status == "paid":
        flash("Payment recorded. This invoice is now paid.", "success")
    else:
        flash(
            "Payment recorded. "
            f"{format_currency(inv.balance_due, inv.currency_code)} remains due.",
            "success",
        )
    return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))


@bp.route("/invoice/<int:invoice_id>/mark-paid", methods=["POST"])
@login_required
def invoice_mark_paid(invoice_id: int):
    inv = _own_invoice_for_update(invoice_id)
    if inv.status == "void":
        flash("A void invoice cannot be marked paid.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))
    if inv.status == "paid" or inv.balance_due <= 0:
        inv.sync_payment_status()
        db.session.commit()
        flash("Invoice is already paid.", "info")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    now = datetime.now(timezone.utc)
    db.session.add(
        InvoicePayment(
            invoice=inv,
            amount=inv.balance_due,
            paid_at=now,
            method="manual",
            note="Marked paid manually",
        )
    )
    db.session.flush()
    inv.sync_payment_status(changed_at=now)
    db.session.commit()
    flash("Invoice marked paid.", "success")
    return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))


@bp.route("/invoice/<int:invoice_id>/void", methods=["POST"])
@login_required
def invoice_void(invoice_id: int):
    inv = _own_invoice_for_update(invoice_id)
    if inv.status == "void":
        flash("Invoice is already void.", "info")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))
    if inv.status == "paid":
        flash("A paid invoice cannot be voided.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))
    if inv.amount_paid > 0:
        flash(
            "An invoice with recorded payments cannot be voided. "
            "Reconcile those payments first.",
            "error",
        )
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    inv.status = "void"
    inv.voided_at = datetime.now(timezone.utc)
    inv.paid_at = None
    db.session.commit()
    flash("Invoice voided. Payment reminders have been stopped.", "success")
    return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
def invoice_edit(invoice_id: int):
    inv = (
        _own_invoice_for_update(invoice_id)
        if request.method == "POST"
        else _own_invoice(invoice_id)
    )
    if inv.status in {"paid", "void"}:
        flash("Paid and void invoices cannot be edited.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    if request.method == "GET":
        return _render_invoice_edit(inv, _invoice_form_data(inv))

    logo_filename = None
    accent_color = "#1e3a8a"
    remove_footer = False
    branding = current_user.branding
    if branding and is_pro():
        logo_filename = branding.logo_filename
        raw_accent = branding.accent_color or "#1e3a8a"
        accent_color = (
            raw_accent
            if re.fullmatch(r"#[0-9a-fA-F]{6}", raw_accent)
            else "#1e3a8a"
        )
        remove_footer = bool(branding.remove_footer)

    theme = request.form.get("theme", "default")
    if theme not in ALLOWED_THEMES or (theme != "default" and not is_pro()):
        theme = "default"

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
        return _render_invoice_edit(inv, request.form)
    context["remove_footer"] = remove_footer

    if not context["invoice_number"].strip():
        flash("Invoice number is required.", "error")
        return _render_invoice_edit(inv, request.form)
    if not context.get("line_items"):
        flash("Please add at least one line item with a description.", "error")
        return _render_invoice_edit(inv, request.form)
    if invoice_number_exists(
        current_user.id,
        context["invoice_number"],
        exclude_id=inv.id,
    ):
        flash(
            "That invoice number is already in use. Choose a different number.",
            "error",
        )
        return _render_invoice_edit(inv, request.form)

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
        return _render_invoice_edit(inv, request.form)

    recorded_payments = inv.amount_paid
    if (
        recorded_payments > 0
        and context["currency_code"]
        != normalize_currency_code(inv.currency_code)
    ):
        flash(
            "Currency cannot be changed after a payment has been recorded.",
            "error",
        )
        return _render_invoice_edit(inv, request.form)
    new_total = shadow_values["total_decimal"]
    if new_total < recorded_payments:
        flash(
            "Invoice total cannot be less than payments already recorded.",
            "error",
        )
        return _render_invoice_edit(inv, request.form)

    due_date_changed = inv.due_date != context["due_date"]
    inv.client_id = selected_client_id
    inv.invoice_number = context["invoice_number"]
    inv.currency_code = context["currency_code"]
    inv.invoice_date = context["invoice_date"]
    inv.due_date = context["due_date"]
    inv.from_company = context["from_company"]
    inv.from_address = context["from_address"]
    inv.from_email = context["from_email"]
    inv.from_phone = context["from_phone"]
    inv.to_name = context["to_name"]
    inv.to_address = context["to_address"]
    inv.to_email = context["to_email"]
    inv.line_items_json = json.dumps(context["line_items"])
    inv.tax_rate = context["tax_rate"]
    inv.discount = context["discount"]
    inv.subtotal = context["subtotal"]
    inv.total = context["total"]
    inv.invoice_date_value = shadow_values["invoice_date_value"]
    inv.due_date_value = shadow_values["due_date_value"]
    inv.tax_rate_decimal = shadow_values["tax_rate_decimal"]
    inv.discount_decimal = shadow_values["discount_decimal"]
    inv.subtotal_decimal = shadow_values["subtotal_decimal"]
    inv.total_decimal = shadow_values["total_decimal"]
    inv.notes = context["notes"]
    inv.payment_info = context["payment_info"]
    inv.payment_url = context["payment_url"]
    inv.logo_filename = logo_filename
    inv.theme = theme
    if due_date_changed:
        inv.reminder_3d_sent = False
        inv.reminder_0d_sent = False
        inv.reminder_7d_sent = False
        InvoiceDelivery.query.filter(
            InvoiceDelivery.invoice_id == inv.id,
            InvoiceDelivery.delivery_kind.in_(
                {"reminder_3d", "reminder_0d", "reminder_7d"}
            ),
        ).delete(synchronize_session=False)

    # A revision never resets a sent invoice to draft. Invoices with recorded
    # payments may need to move between partial and paid when the total changes.
    # Avoid syncing unpaid zero-total drafts, which are still editable drafts.
    if recorded_payments > 0:
        inv.sync_payment_status()

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if invoice_number_exists(
            current_user.id,
            context["invoice_number"],
            exclude_id=inv.id,
        ):
            flash(
                "That invoice number was just used by another request. "
                "Choose a different number.",
                "error",
            )
            inv = _own_invoice(invoice_id)
            return _render_invoice_edit(inv, request.form)
        raise

    flash("Invoice updated.", "success")
    return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))


# ---------------------------------------------------------------------------
# Download (re-generate PDF from stored data)
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>/download")
@login_required
@limiter.limit("10 per minute")
def invoice_download(invoice_id: int):
    inv = _own_invoice(invoice_id)
    context = context_from_invoice(inv)
    pdf_bytes = render_pdf(context, theme=inv.theme or "default")

    safe_number  = _safe_filename(inv.invoice_number)
    filename     = f"Invoice-{safe_number}.pdf"
    encoded      = quote(filename, safe="")
    content_disp = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded}'

    response = make_response(pdf_bytes)
    response.headers["Content-Type"]        = "application/pdf"
    response.headers["Content-Disposition"] = content_disp
    response.headers["Cache-Control"]       = "no-store"
    return response


# ---------------------------------------------------------------------------
# Duplicate
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>/duplicate", methods=["POST"])
@login_required
def invoice_duplicate(invoice_id: int):
    orig = _own_invoice(invoice_id)
    try:
        shadow_values = invoice_shadow_values(
            invoice_date=orig.invoice_date,
            due_date=orig.due_date,
            tax_rate=orig.tax_rate,
            discount=orig.discount,
            subtotal=orig.subtotal,
            total=orig.total,
        )
    except FinancialShadowValueError:
        current_app.logger.warning(
            "Invoice duplication rejected because legacy financial data "
            "cannot populate typed shadow columns",
            extra={"invoice_id": orig.id},
        )
        flash(
            "This invoice contains invalid legacy financial or date data "
            "and cannot be duplicated.",
            "error",
        )
        return redirect(url_for("dashboard.invoice_detail", invoice_id=orig.id))

    duplicate_number = next_available_invoice_number(
        current_user.id,
        f"{orig.invoice_number}-copy",
    )
    dup  = Invoice(
        user_id         = current_user.id,
        client_id       = orig.client_id,
        invoice_number  = duplicate_number,
        currency_code   = normalize_currency_code(orig.currency_code),
        invoice_date    = orig.invoice_date,
        due_date        = orig.due_date,
        from_company    = orig.from_company,
        from_address    = orig.from_address,
        from_email      = orig.from_email,
        from_phone      = orig.from_phone,
        to_name         = orig.to_name,
        to_address      = orig.to_address,
        to_email        = orig.to_email,
        line_items_json = orig.line_items_json,
        tax_rate        = orig.tax_rate,
        discount        = orig.discount,
        subtotal        = orig.subtotal,
        total           = orig.total,
        invoice_date_value=shadow_values["invoice_date_value"],
        due_date_value=shadow_values["due_date_value"],
        tax_rate_decimal=shadow_values["tax_rate_decimal"],
        discount_decimal=shadow_values["discount_decimal"],
        subtotal_decimal=shadow_values["subtotal_decimal"],
        total_decimal=shadow_values["total_decimal"],
        notes           = orig.notes,
        payment_info    = orig.payment_info,
        payment_url     = orig.payment_url,
        logo_filename   = orig.logo_filename,
        theme           = orig.theme,
        status          = "draft",
    )
    db.session.add(dup)
    db.session.commit()
    flash("Invoice duplicated.", "success")
    return redirect(url_for("dashboard.invoice_detail", invoice_id=dup.id))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>/delete", methods=["POST"])
@login_required
def invoice_delete(invoice_id: int):
    inv = _own_invoice(invoice_id)
    db.session.delete(inv)
    db.session.commit()
    flash("Invoice deleted.", "info")
    return redirect(url_for("dashboard.index"))


# ---------------------------------------------------------------------------
# Public link management
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>/public-link/rotate", methods=["POST"])
@login_required
def invoice_public_link_rotate(invoice_id: int):
    inv = _own_invoice(invoice_id)
    inv.view_token = secrets.token_urlsafe(32)
    db.session.commit()
    flash("Public invoice link rotated. The previous link no longer works.", "success")
    return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))


@bp.route("/invoice/<int:invoice_id>/public-link/revoke", methods=["POST"])
@login_required
def invoice_public_link_revoke(invoice_id: int):
    inv = _own_invoice(invoice_id)
    inv.view_token = None
    db.session.commit()
    flash("Public invoice link revoked.", "success")
    return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))


# ---------------------------------------------------------------------------
# Send via email (Pro only)
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>/send", methods=["POST"])
@login_required
@pro_required
@limiter.limit("10 per hour")
def invoice_send(invoice_id: int):
    inv = _own_invoice(invoice_id)
    if inv.status == "void":
        flash("A void invoice cannot be sent.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    recipient = normalize_email(
        request.form.get("recipient_email", "").strip() or inv.to_email
    )
    if not is_valid_email(recipient):
        flash("A valid recipient email address is required.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    # Ensure this invoice has a view token for tracking
    if not inv.view_token:
        inv.view_token = secrets.token_urlsafe(32)

    context   = context_from_invoice(inv)
    pdf_bytes = render_pdf(context, theme=inv.theme or "default")

    safe_number = _safe_filename(inv.invoice_number)
    filename    = f"Invoice-{safe_number}.pdf"
    view_url    = external_url("public.invoice_view", token=inv.view_token)

    body = render_template(
        "emails/invoice_body.txt",
        invoice=inv,
        sender_name=inv.from_company or current_user.email,
        view_url=view_url,
    )

    msg = Message(
        subject=f"Invoice {inv.invoice_number} from {inv.from_company or 'PDFBillr'}",
        recipients=[recipient],
        body=body,
    )
    msg.attach(filename, "application/pdf", pdf_bytes)

    try:
        mail.send(msg)
    except Exception as exc:
        current_app.logger.error(
            "Invoice email failed: invoice=%s error=%s",
            inv.id,
            type(exc).__name__,
        )
        flash("Failed to send email. Please check your mail configuration.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    inv.sent_at = datetime.now(timezone.utc)
    if inv.status not in {"partial", "paid"}:
        inv.status = "sent"
    db.session.commit()

    flash(f"Invoice sent to {recipient}.", "success")
    return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))


# ---------------------------------------------------------------------------
# Branding (Pro only)
# ---------------------------------------------------------------------------

@bp.route("/branding", methods=["GET", "POST"])
@login_required
@pro_required
def branding():
    profile: BrandingProfile | None = current_user.branding

    if request.method == "POST":
        if profile is None:
            profile = BrandingProfile(user_id=current_user.id)
            db.session.add(profile)

        # Accent color — validate strict hex to prevent CSS injection
        accent = request.form.get("accent_color", "#1e3a8a").strip()
        if not re.match(r'^#[0-9a-fA-F]{6}$', accent):
            flash("Invalid accent color. Use a 6-digit hex color (e.g. #1e3a8a).", "error")
            return render_template("dashboard/branding.html", profile=profile)
        profile.accent_color = accent

        # Remove footer toggle
        profile.remove_footer = bool(request.form.get("remove_footer"))

        # Logo upload
        logo_file = request.files.get("logo")
        if logo_file and logo_file.filename:
            try:
                new_name = store_logo(
                    logo_file,
                    user_id=current_user.id,
                    upload_folder=current_app.config["UPLOAD_FOLDER"],
                    max_pixels=current_app.config["MAX_LOGO_PIXELS"],
                    max_dimension=current_app.config["MAX_LOGO_DIMENSION"],
                )
            except LogoValidationError as exc:
                flash(str(exc), "error")
                return render_template("dashboard/branding.html", profile=profile)
            old_name = profile.logo_filename
            profile.logo_filename = new_name
        else:
            old_name = None

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            if logo_file and logo_file.filename:
                delete_user_logo(
                    new_name,
                    user_id=current_user.id,
                    upload_folder=current_app.config["UPLOAD_FOLDER"],
                )
            current_app.logger.exception(
                "Failed to save branding for user %s", current_user.id
            )
            flash("Branding could not be saved. Please try again.", "error")
            return render_template("dashboard/branding.html", profile=profile)

        if old_name:
            delete_user_logo(
                old_name,
                user_id=current_user.id,
                upload_folder=current_app.config["UPLOAD_FOLDER"],
            )
        flash("Branding saved.", "success")
        return redirect(url_for("dashboard.branding"))

    return render_template("dashboard/branding.html", profile=profile)


@bp.route("/branding/logo")
@login_required
@pro_required
def branding_logo():
    profile = current_user.branding
    if not profile or not profile.logo_filename:
        abort(404)
    path = resolve_logo_path(
        profile.logo_filename,
        upload_folder=current_app.config["UPLOAD_FOLDER"],
        legacy_logo_folder=os.path.join(current_app.root_path, "static", "logos"),
    )
    if not path:
        abort(404)
    return send_file(path, conditional=False)


# ---------------------------------------------------------------------------
# Payment reminder preferences (Pro only)
# ---------------------------------------------------------------------------

@bp.route("/reminders", methods=["GET", "POST"])
@login_required
@pro_required
def reminder_settings():
    preference: ReminderPreference | None = current_user.reminder_preference
    if preference is None:
        preference = ReminderPreference(user_id=current_user.id)

    if request.method == "POST":
        try:
            before_due_days = _optional_bounded_int(
                request.form.get("before_due_days"),
                minimum=1,
                maximum=30,
                label="Before-due offset",
            )
            overdue_days = _optional_bounded_int(
                request.form.get("overdue_days"),
                minimum=1,
                maximum=90,
                label="Overdue offset",
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template(
                "dashboard/reminder_settings.html",
                preference=preference,
                form_data=request.form,
            ), 400

        if preference.id is None:
            db.session.add(preference)
        preference.enabled = "enabled" in request.form
        preference.before_due_days = before_due_days
        preference.on_due_date = "on_due_date" in request.form
        preference.overdue_days = overdue_days
        db.session.commit()
        flash("Payment reminder settings saved.", "success")
        return redirect(url_for("dashboard.reminder_settings"))

    return render_template(
        "dashboard/reminder_settings.html",
        preference=preference,
        form_data=None,
    )


# ---------------------------------------------------------------------------
# Save Draft (no PDF generation)
# ---------------------------------------------------------------------------

@bp.route("/save-draft", methods=["POST"])
@login_required
def save_draft():
    from blueprints.public import _HEX_RE, _save_invoice
    from utils.pdf import build_invoice_context

    logo_filename = None
    accent_color  = "#1e3a8a"
    remove_footer = False

    branding = current_user.branding
    if branding and is_pro():
        logo_filename = branding.logo_filename
        raw_accent    = branding.accent_color or "#1e3a8a"
        accent_color  = raw_accent if _HEX_RE.match(raw_accent) else "#1e3a8a"
        remove_footer = branding.remove_footer

    theme = request.form.get("theme", "default")
    if theme not in ALLOWED_THEMES or (theme != "default" and not is_pro()):
        theme = "default"

    try:
        context = build_invoice_context(
            request.form,
            logo_filename=logo_filename,
            accent_color=accent_color,
        )
    except (InvoiceCalculationError, PaymentURLValidationError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("public.index"))
    context["remove_footer"] = remove_footer

    if not context.get("line_items"):
        flash("Please add at least one line item to save a draft.", "error")
        return redirect(url_for("public.index"))

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
        return redirect(url_for("public.index"))

    if not _save_invoice(context, theme, shadow_values):
        flash(
            "That invoice number is already in use. Choose a different number.",
            "error",
        )
        return redirect(url_for("public.index"))
    flash("Draft saved.", "success")
    return redirect(url_for("dashboard.index"))


# ---------------------------------------------------------------------------
# Recurring Invoices (Pro only)
# ---------------------------------------------------------------------------

@bp.route("/recurring")
@login_required
@pro_required
def recurring_list():
    templates = (
        RecurringInvoice.query
        .filter_by(user_id=current_user.id)
        .order_by(RecurringInvoice.created_at.desc())
        .all()
    )
    return render_template("dashboard/recurring_list.html", templates=templates)


@bp.route("/recurring/new", methods=["GET", "POST"])
@login_required
@pro_required
def recurring_new():
    if request.method == "POST":
        tmpl = _save_recurring_template(None)
        if tmpl:
            flash("Recurring invoice created.", "success")
            return redirect(url_for("dashboard.recurring_list"))
        return render_template("dashboard/recurring_form.html", tmpl=None,
                               intervals=_VALID_INTERVALS)

    return render_template("dashboard/recurring_form.html", tmpl=None,
                           intervals=_VALID_INTERVALS)


@bp.route("/recurring/<int:tmpl_id>/edit", methods=["GET", "POST"])
@login_required
@pro_required
def recurring_edit(tmpl_id: int):
    tmpl = _own_recurring(tmpl_id)
    if request.method == "POST":
        updated = _save_recurring_template(tmpl)
        if updated:
            flash("Recurring invoice updated.", "success")
            return redirect(url_for("dashboard.recurring_list"))
        return render_template("dashboard/recurring_form.html", tmpl=tmpl,
                               intervals=_VALID_INTERVALS)
    return render_template("dashboard/recurring_form.html", tmpl=tmpl,
                           intervals=_VALID_INTERVALS)


@bp.route("/recurring/<int:tmpl_id>/toggle", methods=["POST"])
@login_required
@pro_required
def recurring_toggle(tmpl_id: int):
    tmpl = _own_recurring(tmpl_id)
    tmpl.is_active = not tmpl.is_active
    db.session.commit()
    state = "activated" if tmpl.is_active else "paused"
    flash(f"Recurring invoice {state}.", "success")
    return redirect(url_for("dashboard.recurring_list"))


@bp.route("/recurring/<int:tmpl_id>/delete", methods=["POST"])
@login_required
@pro_required
def recurring_delete(tmpl_id: int):
    tmpl = _own_recurring(tmpl_id)
    db.session.delete(tmpl)
    db.session.commit()
    flash("Recurring invoice deleted.", "info")
    return redirect(url_for("dashboard.recurring_list"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dashboard_today() -> date:
    timezone_name = current_app.config.get("SCHEDULER_TIMEZONE", "UTC")
    return datetime.now(ZoneInfo(timezone_name)).date()


def _parse_query_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_collectible(invoice: Invoice) -> bool:
    return (
        invoice.status in {"sent", "finalized"}
        or (invoice.status == "partial" and invoice.sent_at is not None)
    ) and invoice.balance_due > 0


def _payment_business_date(value: datetime, timezone_name: str) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(timezone_name)).date()


def _receivables_metrics(invoices: list[Invoice], *, today: date) -> dict:
    due_soon_through = today + timedelta(days=7)
    timezone_name = current_app.config.get("SCHEDULER_TIMEZONE", "UTC")
    currency_codes = {
        normalize_currency_code(invoice.currency_code)
        for invoice in invoices
    }
    metrics: dict[str, dict[str, Decimal]] = {
        name: {
            currency_code: Decimal("0.00")
            for currency_code in currency_codes
        }
        for name in (
            "outstanding",
            "overdue",
            "due_soon",
            "paid_this_month",
        )
    }

    for invoice in invoices:
        currency_code = getattr(invoice, "currency_code", None) or "USD"
        if _is_collectible(invoice):
            _add_metric_amount(
                metrics["outstanding"],
                currency_code,
                invoice.balance_due,
            )
            due = invoice.due_date_as_date
            if due is not None and due < today:
                _add_metric_amount(
                    metrics["overdue"],
                    currency_code,
                    invoice.balance_due,
                )
            elif due is not None and today <= due <= due_soon_through:
                _add_metric_amount(
                    metrics["due_soon"],
                    currency_code,
                    invoice.balance_due,
                )

        for payment in invoice.payments:
            if (
                payment.paid_at is not None
                and _payment_business_date(
                    payment.paid_at,
                    timezone_name,
                ).replace(day=1)
                == today.replace(day=1)
            ):
                _add_metric_amount(
                    metrics["paid_this_month"],
                    currency_code,
                    payment.amount,
                )

    return {
        name: dict(sorted(amounts.items()))
        for name, amounts in metrics.items()
    }


def _add_metric_amount(
    amounts: dict[str, Decimal],
    currency_code: str,
    amount: Decimal,
) -> None:
    amounts[currency_code] = (
        amounts.get(currency_code, Decimal("0.00")) + amount
    ).quantize(Decimal("0.01"))


def _sort_dashboard_invoices(
    invoices: list[Invoice],
    sort: str,
) -> list[Invoice]:
    if sort == "oldest":
        return sorted(
            invoices,
            key=_invoice_created_timestamp,
        )
    if sort == "due_soonest":
        return sorted(
            invoices,
            key=lambda invoice: (
                invoice.due_date_as_date is None,
                invoice.due_date_as_date or date.max,
                -(invoice.id or 0),
            ),
        )
    if sort == "amount_high":
        return sorted(
            invoices,
            key=lambda invoice: (invoice.total_amount, invoice.id or 0),
            reverse=True,
        )
    if sort == "amount_low":
        return sorted(
            invoices,
            key=lambda invoice: (invoice.total_amount, -(invoice.id or 0)),
        )
    return sorted(
        invoices,
        key=_invoice_created_timestamp,
        reverse=True,
    )


def _invoice_created_timestamp(invoice: Invoice) -> float:
    created_at = invoice.created_at
    if created_at is None:
        return float("-inf")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.timestamp()


def _paginate_items(items: list, *, page: int, per_page: int) -> SimpleNamespace:
    total = len(items)
    pages = math.ceil(total / per_page) if total else 0
    start = (page - 1) * per_page
    return SimpleNamespace(
        items=items[start : start + per_page],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_prev=page > 1,
        has_next=page < pages,
        prev_num=page - 1,
        next_num=page + 1,
    )


def _optional_bounded_int(
    value: str | None,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(
            f"{label} must be between {minimum} and {maximum} days."
        )
    return parsed


def _own_invoice(invoice_id: int) -> Invoice:
    inv = db.session.get(Invoice, invoice_id)
    if inv is None or inv.user_id != current_user.id:
        current_app.logger.warning(
            "Unauthorized invoice access: user=%s invoice=%s", current_user.id, invoice_id
        )
        abort(404)
    return inv


def _own_invoice_for_update(invoice_id: int) -> Invoice:
    """Resolve an owned invoice and lock it while its balance is changed."""
    inv = (
        Invoice.query.filter_by(
            id=invoice_id,
            user_id=current_user.id,
        )
        .with_for_update()
        .first()
    )
    if inv is None:
        current_app.logger.warning(
            "Unauthorized invoice access: user=%s invoice=%s",
            current_user.id,
            invoice_id,
        )
        abort(404)
    return inv


def _payment_amount(
    raw_amount: str | None,
    *,
    currency_code: str = "USD",
) -> Decimal:
    try:
        amount = Decimal((raw_amount or "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid payment amount.") from None

    if not amount.is_finite() or amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")
    step = Decimal(currency_input_step(currency_code))
    if amount.quantize(step) != amount:
        code = normalize_currency_code(currency_code)
        precision = "whole units" if step == 1 else "two decimal places"
        raise ValueError(f"{code} payment amounts must use {precision}.")
    return amount.quantize(step)


def _payment_datetime(raw_date: str | None) -> datetime:
    if not raw_date:
        return datetime.now(timezone.utc)
    try:
        paid_on = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Enter a valid payment date.") from None
    if paid_on > _dashboard_today():
        raise ValueError("Payment date cannot be in the future.")
    timezone_name = current_app.config.get("SCHEDULER_TIMEZONE", "UTC")
    local_midnight = datetime.combine(
        paid_on,
        datetime.min.time(),
        tzinfo=ZoneInfo(timezone_name),
    )
    return local_midnight.astimezone(timezone.utc)


def _render_invoice_edit(inv: Invoice, form_data):
    from blueprints.clients import invoice_form_context

    context = invoice_form_context(form_data)
    context.update(
        {
            "today": inv.invoice_date,
            "form_action": url_for(
                "dashboard.invoice_edit",
                invoice_id=inv.id,
            ),
            "form_title": f"Edit Invoice {inv.invoice_number}",
            "edit_mode": True,
            "invoice": inv,
        }
    )
    return render_template("form.html", **context)


def _invoice_form_data(inv: Invoice) -> MultiDict:
    """Return the persisted invoice in the same multi-value shape as POST data."""
    try:
        line_items = json.loads(inv.line_items_json or "[]")
    except (TypeError, ValueError):
        line_items = []
    if not isinstance(line_items, list):
        line_items = []

    values = MultiDict(
        [
            ("invoice_number", inv.invoice_number or ""),
            ("client_id", str(inv.client_id or "")),
            ("currency_code", normalize_currency_code(inv.currency_code)),
            ("invoice_date", inv.invoice_date or ""),
            ("due_date", inv.due_date or ""),
            ("from_company", inv.from_company or ""),
            ("from_address", inv.from_address or ""),
            ("from_email", inv.from_email or ""),
            ("from_phone", inv.from_phone or ""),
            ("to_name", inv.to_name or ""),
            ("to_address", inv.to_address or ""),
            ("to_email", inv.to_email or ""),
            ("tax_rate", str(inv.tax_rate or 0)),
            ("discount", str(inv.discount or 0)),
            ("notes", inv.notes or ""),
            ("payment_info", inv.payment_info or ""),
            ("payment_url", inv.payment_url or ""),
            ("theme", inv.theme or "default"),
        ]
    )
    for item in line_items:
        if not isinstance(item, dict):
            continue
        values.add("description[]", item.get("description", ""))
        values.add("qty[]", str(item.get("qty", "")))
        values.add("rate[]", str(item.get("rate", "")))
    return values


def _own_recurring(tmpl_id: int) -> RecurringInvoice:
    tmpl = db.session.get(RecurringInvoice, tmpl_id)
    if tmpl is None or tmpl.user_id != current_user.id:
        abort(404)
    return tmpl


def _save_recurring_template(tmpl: RecurringInvoice | None) -> RecurringInvoice | None:
    """Create or update a RecurringInvoice from the current POST request.

    Returns the template on success, None on validation failure (flash set).
    """
    interval = request.form.get("interval", "monthly").strip()
    if interval not in _VALID_INTERVALS:
        flash("Invalid interval.", "error")
        return None

    try:
        next_run_str = request.form.get("next_run_date", "").strip()
        next_run = datetime.strptime(next_run_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid start date.", "error")
        return None

    try:
        net_days = int(request.form.get("net_days", 30))
        if not 0 <= net_days <= 365:
            raise ValueError
    except ValueError:
        flash("Net days must be an integer from 0 to 365.", "error")
        return None

    # Validate and parse line items from JSON submitted by the form
    line_items_raw = request.form.get("line_items_json", "[]").strip()
    try:
        line_items = json.loads(line_items_raw)
        if not isinstance(line_items, list):
            raise ValueError
    except (ValueError, TypeError):
        flash("Invalid line items.", "error")
        return None

    if not line_items:
        flash("Please add at least one line item.", "error")
        return None

    try:
        calculated = calculate_invoice(
            line_items,
            tax_rate=request.form.get("tax_rate", 0),
            discount=request.form.get("discount", 0),
            currency_code=request.form.get("currency_code"),
        )
    except InvoiceCalculationError as exc:
        flash(str(exc), "error")
        return None
    if not calculated.line_items:
        flash("Please add at least one line item.", "error")
        return None

    if tmpl is None:
        tmpl = RecurringInvoice(user_id=current_user.id)
        db.session.add(tmpl)

    tmpl.invoice_number_prefix = request.form.get("invoice_number_prefix", "INV")[:50].strip() or "INV"
    tmpl.currency_code = normalize_currency_code(
        request.form.get("currency_code")
    )
    tmpl.from_company  = request.form.get("from_company", "")[:200]
    tmpl.from_address  = request.form.get("from_address", "")[:1000]
    tmpl.from_email    = request.form.get("from_email", "")[:200]
    tmpl.from_phone    = request.form.get("from_phone", "")[:200]
    tmpl.to_name       = request.form.get("to_name", "")[:200]
    tmpl.to_address    = request.form.get("to_address", "")[:1000]
    to_email = normalize_email(request.form.get("to_email", ""))
    if to_email and not is_valid_email(to_email):
        flash("A valid client email address is required.", "error")
        return None
    tmpl.to_email      = to_email
    tmpl.line_items_json = json.dumps(calculated.line_items)
    tmpl.tax_rate      = float(calculated.tax_rate)
    tmpl.discount      = float(calculated.discount)
    shadow_values = recurring_invoice_shadow_values(
        tax_rate=calculated.tax_rate,
        discount=calculated.discount,
    )
    tmpl.tax_rate_decimal = shadow_values["tax_rate_decimal"]
    tmpl.discount_decimal = shadow_values["discount_decimal"]
    tmpl.notes         = request.form.get("notes", "")[:2000]
    tmpl.payment_info  = request.form.get("payment_info", "")[:2000]
    try:
        tmpl.payment_url = normalize_payment_url(
            request.form.get("payment_url")
        )
    except PaymentURLValidationError as exc:
        flash(str(exc), "error")
        return None
    theme = request.form.get("theme", "default")
    tmpl.theme         = theme if theme in ALLOWED_THEMES else "default"
    tmpl.interval      = interval
    tmpl.net_days      = net_days
    tmpl.next_run_date = next_run
    tmpl.auto_send     = bool(request.form.get("auto_send"))

    db.session.commit()
    return tmpl
