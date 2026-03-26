import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from flask import (
    Blueprint, abort, current_app, flash, make_response,
    redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from flask_mail import Message

from extensions import db, mail
from models import BrandingProfile, Invoice
from utils.gating import is_pro, pro_required
from utils.helpers import _safe_filename
from utils.pdf import ALLOWED_THEMES, context_from_invoice, render_pdf

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

_ALLOWED_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
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
def invoice_send(invoice_id: int):
    inv = _own_invoice(invoice_id)

    recipient = request.form.get("recipient_email", "").strip() or inv.to_email
    if not recipient:
        flash("No recipient email address.", "error")
        return redirect(url_for("dashboard.invoice_detail", invoice_id=inv.id))

    context   = context_from_invoice(inv)
    pdf_bytes = render_pdf(context, theme=inv.theme or "default")

    safe_number = _safe_filename(inv.invoice_number)
    filename    = f"Invoice-{safe_number}.pdf"

    body = render_template(
        "emails/invoice_body.txt",
        invoice=inv,
        sender_name=inv.from_company or current_user.email,
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
            ext = os.path.splitext(logo_file.filename)[1].lower()
            if ext not in _ALLOWED_LOGO_EXTS:
                flash("Logo must be a PNG, JPG, GIF, or WebP image.", "error")
                return render_template("dashboard/branding.html", profile=profile)

            logos_dir = os.path.join(current_app.root_path, "static", "logos")
            # Delete old logo if it was user-uploaded (not the sample assets)
            if profile.logo_filename:
                old_path = os.path.join(logos_dir, profile.logo_filename)
                if os.path.isfile(old_path) and _is_user_logo(profile.logo_filename):
                    os.remove(old_path)

            new_name = f"{current_user.id}_{uuid.uuid4().hex}{ext}"
            logo_file.save(os.path.join(logos_dir, new_name))
            profile.logo_filename = new_name

        db.session.commit()
        flash("Branding saved.", "success")
        return redirect(url_for("dashboard.branding"))

    return render_template("dashboard/branding.html", profile=profile)


# ---------------------------------------------------------------------------
# Save Draft (no PDF generation)
# ---------------------------------------------------------------------------

@bp.route("/save-draft", methods=["POST"])
@login_required
def save_draft():
    from blueprints.public import _HEX_RE, _save_invoice
    from utils.pdf import ALLOWED_THEMES, build_invoice_context

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

    context = build_invoice_context(request.form, logo_filename=logo_filename, accent_color=accent_color)
    context["remove_footer"] = remove_footer

    if not context.get("line_items"):
        flash("Please add at least one line item to save a draft.", "error")
        return redirect(url_for("public.index"))

    _save_invoice(context, theme)
    flash("Draft saved.", "success")
    return redirect(url_for("dashboard.index"))


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


def _is_user_logo(filename: str) -> bool:
    """Return True only for logos uploaded by users (not static sample assets)."""
    sample_names = {"logo.jpg", "landingpagegraphic.png", "filecabinet.png",
                    "banner.png", "pdficon.png", "pdficon2.png", "pdficon3.png", "pdficon4.png"}
    return filename not in sample_names
