import json
import os
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from flask import (
    Blueprint, abort, current_app, flash, make_response,
    redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user, login_required
from flask_mail import Message
from sqlalchemy.exc import SQLAlchemyError

from extensions import db, limiter, mail
from models import BrandingProfile, Invoice, RecurringInvoice
from utils.gating import is_pro, pro_required
from utils.helpers import _safe_filename
from utils.invoice_calculations import InvoiceCalculationError, calculate_invoice
from utils.pdf import ALLOWED_THEMES, context_from_invoice, render_pdf
from utils.uploads import (
    LogoValidationError,
    delete_user_logo,
    resolve_logo_path,
    store_logo,
)
from utils.urls import external_url
from utils.validation import is_valid_email, normalize_email

_VALID_INTERVALS = {"weekly", "biweekly", "monthly", "quarterly"}

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

_PER_PAGE = 20


# ---------------------------------------------------------------------------
# Invoice list
# ---------------------------------------------------------------------------

@bp.route("/")
@login_required
def index():
    page = request.args.get("page", 1, type=int)
    q    = request.args.get("q", "").strip()

    query = current_user.invoices.order_by(Invoice.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Invoice.invoice_number.ilike(like), Invoice.to_name.ilike(like))
        )

    pagination = query.paginate(page=page, per_page=_PER_PAGE, error_out=False)
    return render_template("dashboard/index.html", pagination=pagination, q=q)


# ---------------------------------------------------------------------------
# Invoice detail
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>")
@login_required
def invoice_detail(invoice_id: int):
    inv = _own_invoice(invoice_id)
    return render_template("dashboard/invoice_detail.html", invoice=inv)


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
    dup  = Invoice(
        user_id         = current_user.id,
        invoice_number  = orig.invoice_number + "-copy",
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
        notes           = orig.notes,
        payment_info    = orig.payment_info,
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
# Send via email (Pro only)
# ---------------------------------------------------------------------------

@bp.route("/invoice/<int:invoice_id>/send", methods=["POST"])
@login_required
@pro_required
@limiter.limit("10 per hour")
def invoice_send(invoice_id: int):
    inv = _own_invoice(invoice_id)

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
        current_app.logger.error("Failed to send invoice email: %s", exc)
        flash("Failed to send email. Please check your mail configuration.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    inv.sent_at = datetime.now(timezone.utc)
    inv.status  = "sent"
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
    except InvoiceCalculationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("public.index"))
    context["remove_footer"] = remove_footer

    if not context.get("line_items"):
        flash("Please add at least one line item to save a draft.", "error")
        return redirect(url_for("public.index"))

    _save_invoice(context, theme)
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

def _own_invoice(invoice_id: int) -> Invoice:
    inv = db.session.get(Invoice, invoice_id)
    if inv is None or inv.user_id != current_user.id:
        current_app.logger.warning(
            "Unauthorized invoice access: user=%s invoice=%s", current_user.id, invoice_id
        )
        abort(404)
    return inv


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
    tmpl.notes         = request.form.get("notes", "")[:2000]
    tmpl.payment_info  = request.form.get("payment_info", "")[:2000]
    theme = request.form.get("theme", "default")
    tmpl.theme         = theme if theme in ALLOWED_THEMES else "default"
    tmpl.interval      = interval
    tmpl.net_days      = net_days
    tmpl.next_run_date = next_run
    tmpl.auto_send     = bool(request.form.get("auto_send"))

    db.session.commit()
    return tmpl
