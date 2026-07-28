"""Background job functions for payment reminders and recurring invoices.

These are called by APScheduler once daily. Each function accepts the Flask
app instance and pushes an app context so SQLAlchemy and Flask-Mail work
outside of a request.
"""

import json
import logging
import secrets
from datetime import date, datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta

log = logging.getLogger(__name__)

_INTERVAL_DELTAS = {
    "weekly":    timedelta(weeks=1),
    "biweekly":  timedelta(weeks=2),
    "monthly":   relativedelta(months=1),
    "quarterly": relativedelta(months=3),
}


def send_payment_reminders(app) -> None:
    """Send payment reminder emails for sent Pro invoices with due dates.

    Reminder schedule:
      - 3 days before due date
      - On due date
      - 7 days after due date (overdue)
    """
    with app.app_context():
        from extensions import db, mail
        from flask import render_template
        from flask_mail import Message
        from models import Invoice
        from utils.gating import is_pro

        today = date.today()

        candidates = (
            Invoice.query
            .filter(Invoice.status == "sent")
            .filter(Invoice.due_date.isnot(None))
            .filter(Invoice.due_date != "")
            .all()
        )

        sent_count = 0
        for inv in candidates:
            if not inv.user or not is_pro(inv.user):
                continue
            if not inv.to_email:
                continue

            try:
                due = datetime.strptime(inv.due_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            days_delta = (due - today).days  # negative = overdue
            sender_name = inv.from_company or inv.user.email
            view_url = _view_url_for(app, inv)

            changed = False

            if days_delta == 3 and not inv.reminder_3d_sent:
                changed = _send_reminder(
                    mail, Message, render_template, "emails/reminder_due_soon.txt",
                    inv, sender_name, view_url,
                    f"Invoice {inv.invoice_number} due in 3 days",
                )
                if changed:
                    inv.reminder_3d_sent = True

            elif days_delta == 0 and not inv.reminder_0d_sent:
                changed = _send_reminder(
                    mail, Message, render_template, "emails/reminder_due_today.txt",
                    inv, sender_name, view_url,
                    f"Invoice {inv.invoice_number} is due today",
                )
                if changed:
                    inv.reminder_0d_sent = True

            elif days_delta == -7 and not inv.reminder_7d_sent:
                changed = _send_reminder(
                    mail, Message, render_template, "emails/reminder_overdue.txt",
                    inv, sender_name, view_url,
                    f"Invoice {inv.invoice_number} is overdue",
                )
                if changed:
                    inv.reminder_7d_sent = True

            if changed:
                sent_count += 1

        if sent_count:
            db.session.commit()
        log.info("Payment reminders: sent %d email(s)", sent_count)


def process_recurring_invoices(app) -> None:
    """Generate invoices from active recurring templates that are due today or overdue."""
    with app.app_context():
        from extensions import db, mail
        from flask import render_template
        from flask_mail import Message
        from models import RecurringInvoice
        from utils.gating import is_pro
        from utils.pdf import render_pdf

        today = date.today()

        due_templates = (
            RecurringInvoice.query
            .filter(RecurringInvoice.is_active == True)  # noqa: E712
            .filter(RecurringInvoice.next_run_date <= today)
            .all()
        )

        for tmpl in due_templates:
            if not tmpl.user or not is_pro(tmpl.user):
                log.info(
                    "Skipping recurring template %s because user %s is not Pro",
                    tmpl.id,
                    tmpl.user_id,
                )
                continue
            try:
                inv = _generate_from_template(tmpl, today, db)
                # Commit the occurrence and advance its schedule before any
                # external email side effect. A rerun cannot create it again.
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                log.error(
                    "Failed to generate recurring invoice for template %s (user %s): %s",
                    tmpl.id, tmpl.user_id, exc,
                )
                continue

            if tmpl.auto_send and tmpl.to_email:
                try:
                    _send_generated_invoice(
                        inv,
                        mail,
                        Message,
                        render_template,
                        render_pdf,
                    )
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    log.warning(
                        "Auto-send failed for recurring invoice %s: %s",
                        inv.invoice_number,
                        exc,
                    )
        log.info("Recurring invoices: processed %d template(s)", len(due_templates))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_reminder(mail_obj, Message, render_template_fn, template,
                   inv, sender_name, view_url, subject) -> bool:
    body = render_template_fn(
        template,
        invoice=inv,
        sender_name=sender_name,
        view_url=view_url,
    )
    msg = Message(subject=subject, recipients=[inv.to_email], body=body)
    try:
        mail_obj.send(msg)
    except Exception as exc:
        log.warning("Reminder email failed for invoice %s: %s", inv.id, exc)
        return False
    return True


def _view_url_for(app, inv) -> str:
    """Return the public view URL for an invoice, generating a token if needed."""
    if not inv.view_token:
        inv.view_token = secrets.token_urlsafe(32)
    from utils.urls import external_url

    return external_url("public.invoice_view", token=inv.view_token)


def _next_run_date(current: date, interval: str) -> date:
    delta = _INTERVAL_DELTAS.get(interval, relativedelta(months=1))
    return current + delta


def _generate_from_template(tmpl, today, db):
    """Create and stage one Invoice occurrence from a recurring template."""
    from models import Invoice
    from utils.invoice_calculations import calculate_invoice

    line_items = json.loads(tmpl.line_items_json or "[]")
    calculated = calculate_invoice(
        line_items,
        tax_rate=tmpl.tax_rate,
        discount=tmpl.discount,
    )
    financials = calculated.template_values()

    prefix = (tmpl.invoice_number_prefix or "INV").rstrip("-")
    existing_count = Invoice.query.filter_by(user_id=tmpl.user_id).count()
    invoice_number = f"{prefix}-{today.year}-{existing_count + 1:03d}"

    due_date_obj = (
        today + timedelta(days=tmpl.net_days)
        if tmpl.net_days is not None
        else None
    )

    inv = Invoice(
        user_id         = tmpl.user_id,
        invoice_number  = invoice_number,
        invoice_date    = today.isoformat(),
        due_date        = due_date_obj.isoformat() if due_date_obj else None,
        from_company    = tmpl.from_company,
        from_address    = tmpl.from_address,
        from_email      = tmpl.from_email,
        from_phone      = tmpl.from_phone,
        to_name         = tmpl.to_name,
        to_address      = tmpl.to_address,
        to_email        = tmpl.to_email,
        line_items_json = json.dumps(calculated.line_items),
        tax_rate        = financials["tax_rate"],
        discount        = financials["discount"],
        subtotal        = financials["subtotal"],
        total           = financials["total"],
        notes           = tmpl.notes,
        payment_info    = tmpl.payment_info,
        theme           = tmpl.theme or "default",
        status          = "draft",
        view_token      = secrets.token_urlsafe(32),
    )
    db.session.add(inv)
    db.session.flush()

    tmpl.last_run_date = today
    tmpl.next_run_date = _next_run_date(today, tmpl.interval)
    return inv


def _send_generated_invoice(
    inv,
    mail_obj,
    Message,
    render_template_fn,
    render_pdf_fn,
) -> None:
    from utils.helpers import _safe_filename
    from utils.pdf import context_from_invoice
    from utils.urls import external_url

    context = context_from_invoice(inv)
    pdf_bytes = render_pdf_fn(context, theme=inv.theme or "default")
    safe_number = _safe_filename(inv.invoice_number)
    filename = f"Invoice-{safe_number}.pdf"
    sender_name = inv.from_company or (inv.user.email if inv.user else "PDFBillr")
    view_url = external_url("public.invoice_view", token=inv.view_token)
    body = render_template_fn(
        "emails/invoice_body.txt",
        invoice=inv,
        sender_name=sender_name,
        view_url=view_url,
    )
    msg = Message(
        subject=f"Invoice {inv.invoice_number} from {sender_name}",
        recipients=[inv.to_email],
        body=body,
    )
    msg.attach(filename, "application/pdf", pdf_bytes)
    mail_obj.send(msg)
    inv.status = "sent"
    inv.sent_at = datetime.now(timezone.utc)
