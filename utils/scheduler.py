"""Background job functions for payment reminders and recurring invoices.

These are called by APScheduler once daily. Each function accepts the Flask
app instance and pushes an app context so SQLAlchemy and Flask-Mail work
outside of a request.
"""

import json
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

log = logging.getLogger(__name__)

_INTERVAL_DELTAS = {
    "weekly": timedelta(weeks=1),
    "biweekly": timedelta(weeks=2),
    "monthly": relativedelta(months=1),
    "quarterly": relativedelta(months=3),
}

_REMINDER_SPECS = {
    "reminder_3d": (
        "reminder_3d_sent",
        "emails/reminder_due_soon.txt",
        lambda inv: f"Invoice {inv.invoice_number} due in 3 days",
    ),
    "reminder_0d": (
        "reminder_0d_sent",
        "emails/reminder_due_today.txt",
        lambda inv: f"Invoice {inv.invoice_number} is due today",
    ),
    "reminder_7d": (
        "reminder_7d_sent",
        "emails/reminder_overdue.txt",
        lambda inv: f"Invoice {inv.invoice_number} is overdue",
    ),
}
_DELIVERY_LEASE = timedelta(minutes=15)


class PermanentDeliveryError(ValueError):
    """The queued delivery can no longer become valid on retry."""


def send_payment_reminders(app) -> None:
    """Enqueue and deliver due payment reminders.

    Reminder schedule:
      - 3 days before due date
      - On due date
      - 7 days after due date (overdue)

    The durable delivery row is committed before SMTP is attempted. Failed
    deliveries remain retryable even after the trigger date has passed.
    """
    with app.app_context():
        from extensions import db, mail
        from flask import render_template
        from flask_mail import Message
        from models import Invoice, InvoiceDelivery
        from utils.gating import is_pro

        today = _business_today(app)

        candidates = (
            Invoice.query.filter(Invoice.status == "sent")
            .filter(Invoice.due_date.isnot(None))
            .filter(Invoice.due_date != "")
            .all()
        )

        enqueued_count = 0
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
            delivery_kind = None
            if days_delta == 3 and not inv.reminder_3d_sent:
                delivery_kind = "reminder_3d"
            elif days_delta == 0 and not inv.reminder_0d_sent:
                delivery_kind = "reminder_0d"
            elif days_delta == -7 and not inv.reminder_7d_sent:
                delivery_kind = "reminder_7d"

            if delivery_kind:
                _view_url_for(inv)
                if _ensure_invoice_delivery(
                    db, InvoiceDelivery, inv.id, delivery_kind
                ):
                    enqueued_count += 1

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            log.exception("Could not persist payment reminder delivery queue")
            return

        sent_count = _dispatch_invoice_deliveries(
            db,
            mail,
            Message,
            render_template,
            delivery_kinds=set(_REMINDER_SPECS),
        )
        log.info(
            "Payment reminders: enqueued %d, delivered %d",
            enqueued_count,
            sent_count,
        )


def process_recurring_invoices(app) -> None:
    """Generate one durable occurrence for each due recurring template."""
    with app.app_context():
        from extensions import db, mail
        from flask import render_template
        from flask_mail import Message
        from models import InvoiceDelivery, RecurringInvoice, RecurringOccurrence
        from utils.gating import is_pro
        from utils.pdf import render_pdf

        today = _business_today(app)

        due_templates = (
            RecurringInvoice.query.filter(
                RecurringInvoice.is_active == True  # noqa: E712
            )
            .filter(RecurringInvoice.next_run_date <= today)
            .all()
        )

        generated_count = 0
        for tmpl in due_templates:
            if not tmpl.user or not is_pro(tmpl.user):
                log.info(
                    "Skipping recurring template %s because user %s is not Pro",
                    tmpl.id,
                    tmpl.user_id,
                )
                continue

            scheduled_for = tmpl.next_run_date
            if RecurringOccurrence.query.filter_by(
                recurring_invoice_id=tmpl.id,
                scheduled_for=scheduled_for,
            ).first():
                # A previous process committed this occurrence. Advance a stale
                # template pointer without creating a second invoice.
                tmpl.last_run_date = scheduled_for
                tmpl.next_run_date = _next_run_date(
                    scheduled_for, tmpl.interval
                )
                db.session.commit()
                continue

            try:
                occurrence = RecurringOccurrence(
                    recurring_invoice_id=tmpl.id,
                    scheduled_for=scheduled_for,
                )
                db.session.add(occurrence)
                db.session.flush()

                inv = _generate_from_template(
                    tmpl,
                    scheduled_for,
                    db,
                )
                occurrence.invoice_id = inv.id
                if tmpl.auto_send and tmpl.to_email:
                    db.session.add(
                        InvoiceDelivery(
                            invoice_id=inv.id,
                            delivery_kind="recurring_auto_send",
                        )
                    )
                db.session.commit()
                generated_count += 1
            except IntegrityError:
                db.session.rollback()
                log.info(
                    "Recurring occurrence already claimed: template=%s date=%s",
                    tmpl.id,
                    scheduled_for,
                )
                continue
            except (SQLAlchemyError, ValueError, TypeError, json.JSONDecodeError):
                db.session.rollback()
                log.exception(
                    "Failed recurring generation: template=%s user=%s",
                    tmpl.id,
                    tmpl.user_id,
                )

        delivered_count = _dispatch_invoice_deliveries(
            db,
            mail,
            Message,
            render_template,
            render_pdf_fn=render_pdf,
            delivery_kinds={"recurring_auto_send"},
        )
        log.info(
            "Recurring invoices: due %d, generated %d, delivered %d",
            len(due_templates),
            generated_count,
            delivered_count,
        )


def retry_invoice_deliveries(app) -> int:
    """Retry queued reminder and recurring invoice email deliveries."""
    with app.app_context():
        from extensions import db, mail
        from flask import render_template
        from flask_mail import Message
        from utils.pdf import render_pdf

        return _dispatch_invoice_deliveries(
            db,
            mail,
            Message,
            render_template,
            render_pdf_fn=render_pdf,
            delivery_kinds={
                *_REMINDER_SPECS,
                "recurring_auto_send",
            },
        )


# ---------------------------------------------------------------------------
# Delivery helpers
# ---------------------------------------------------------------------------


def _ensure_invoice_delivery(db_obj, Delivery, invoice_id, delivery_kind) -> bool:
    if Delivery.query.filter_by(
        invoice_id=invoice_id,
        delivery_kind=delivery_kind,
    ).first():
        return False

    try:
        with db_obj.session.begin_nested():
            db_obj.session.add(
                Delivery(
                    invoice_id=invoice_id,
                    delivery_kind=delivery_kind,
                )
            )
            db_obj.session.flush()
    except IntegrityError:
        return False
    return True


def _dispatch_invoice_deliveries(
    db_obj,
    mail_obj,
    Message,
    render_template_fn,
    *,
    delivery_kinds,
    render_pdf_fn=None,
) -> int:
    from models import InvoiceDelivery

    now = datetime.now(timezone.utc)
    stale_before = now - _DELIVERY_LEASE
    eligible = or_(
        InvoiceDelivery.status.in_(("pending", "failed")),
        (
            (InvoiceDelivery.status == "sending")
            & (InvoiceDelivery.last_attempt_at < stale_before)
        ),
    )
    delivery_ids = [
        row.id
        for row in (
            InvoiceDelivery.query.filter(
                InvoiceDelivery.delivery_kind.in_(delivery_kinds),
                eligible,
            )
            .order_by(InvoiceDelivery.created_at, InvoiceDelivery.id)
            .limit(100)
            .all()
        )
    ]

    sent_count = 0
    for delivery_id in delivery_ids:
        claimed = (
            InvoiceDelivery.query.filter(
                InvoiceDelivery.id == delivery_id,
                eligible,
            )
            .update(
                {
                    InvoiceDelivery.status: "sending",
                    InvoiceDelivery.attempt_count: (
                        InvoiceDelivery.attempt_count + 1
                    ),
                    InvoiceDelivery.last_attempt_at: now,
                    InvoiceDelivery.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        db_obj.session.commit()
        if claimed != 1:
            continue

        delivery = db_obj.session.get(InvoiceDelivery, delivery_id)
        try:
            _send_invoice_delivery(
                delivery,
                mail_obj,
                Message,
                render_template_fn,
                render_pdf_fn=render_pdf_fn,
            )
        except PermanentDeliveryError as exc:
            db_obj.session.rollback()
            delivery = db_obj.session.get(InvoiceDelivery, delivery_id)
            delivery.status = "discarded"
            delivery.last_error = type(exc).__name__
            delivery.updated_at = datetime.now(timezone.utc)
            db_obj.session.commit()
            log.info(
                "Invoice delivery discarded: id=%s kind=%s",
                delivery.id,
                delivery.delivery_kind,
            )
            continue
        except Exception as exc:  # SMTP/PDF adapters expose varied exceptions.
            db_obj.session.rollback()
            delivery = db_obj.session.get(InvoiceDelivery, delivery_id)
            delivery.status = "failed"
            delivery.last_error = type(exc).__name__[:500]
            delivery.updated_at = datetime.now(timezone.utc)
            db_obj.session.commit()
            log.warning(
                "Invoice delivery failed: id=%s kind=%s error=%s",
                delivery.id,
                delivery.delivery_kind,
                type(exc).__name__,
            )
            continue

        delivery.status = "sent"
        delivery.sent_at = datetime.now(timezone.utc)
        delivery.last_error = None
        delivery.updated_at = delivery.sent_at
        db_obj.session.commit()
        sent_count += 1

    return sent_count


def _send_invoice_delivery(
    delivery,
    mail_obj,
    Message,
    render_template_fn,
    *,
    render_pdf_fn,
) -> None:
    from utils.gating import is_pro

    inv = delivery.invoice
    if inv is None or not inv.to_email:
        raise PermanentDeliveryError(
            "delivery invoice or recipient is unavailable"
        )
    if inv.user is None or not is_pro(inv.user):
        raise PermanentDeliveryError("delivery owner is no longer eligible")

    if delivery.delivery_kind == "recurring_auto_send":
        if render_pdf_fn is None:
            raise RuntimeError("PDF renderer is required for recurring delivery")
        _send_generated_invoice(
            inv,
            mail_obj,
            Message,
            render_template_fn,
            render_pdf_fn,
        )
        return

    try:
        flag_name, template, subject_factory = _REMINDER_SPECS[
            delivery.delivery_kind
        ]
    except KeyError as exc:
        raise PermanentDeliveryError(
            "unknown invoice delivery kind"
        ) from exc

    _send_reminder(
        mail_obj,
        Message,
        render_template_fn,
        template,
        inv,
        inv.from_company or inv.user.email,
        _view_url_for(inv),
        subject_factory(inv),
    )
    setattr(inv, flag_name, True)


def _send_reminder(
    mail_obj,
    Message,
    render_template_fn,
    template,
    inv,
    sender_name,
    view_url,
    subject,
) -> None:
    body = render_template_fn(
        template,
        invoice=inv,
        sender_name=sender_name,
        view_url=view_url,
    )
    msg = Message(subject=subject, recipients=[inv.to_email], body=body)
    mail_obj.send(msg)


def _business_today(app) -> date:
    """Return the scheduler's configured business date."""
    timezone_name = app.config.get("SCHEDULER_TIMEZONE", "UTC")
    return datetime.now(ZoneInfo(timezone_name)).date()


def _view_url_for(inv) -> str:
    """Return the public view URL for an invoice, generating a token if needed."""
    if not inv.view_token:
        inv.view_token = secrets.token_urlsafe(32)
    from utils.urls import external_url

    return external_url("public.invoice_view", token=inv.view_token)


def _next_run_date(current: date, interval: str) -> date:
    delta = _INTERVAL_DELTAS.get(interval, relativedelta(months=1))
    return current + delta


def _generate_from_template(tmpl, scheduled_for, db):
    """Create and stage one Invoice occurrence from a recurring template."""
    from utils.financial_shadow_values import invoice_shadow_values
    from utils.invoice_calculations import calculate_invoice
    from utils.invoice_numbers import next_available_invoice_number

    line_items = json.loads(tmpl.line_items_json or "[]")
    calculated = calculate_invoice(
        line_items,
        tax_rate=tmpl.tax_rate,
        discount=tmpl.discount,
    )
    financials = calculated.template_values()

    prefix = (tmpl.invoice_number_prefix or "INV").rstrip("-")
    from models import Invoice

    existing_count = Invoice.query.filter_by(user_id=tmpl.user_id).count()
    preferred_number = (
        f"{prefix}-{scheduled_for.year}-{existing_count + 1:03d}"
    )
    invoice_number = next_available_invoice_number(
        tmpl.user_id,
        preferred_number,
    )

    due_date_obj = (
        scheduled_for + timedelta(days=tmpl.net_days)
        if tmpl.net_days is not None
        else None
    )
    shadow_values = invoice_shadow_values(
        invoice_date=scheduled_for,
        due_date=due_date_obj,
        tax_rate=calculated.tax_rate,
        discount=calculated.discount,
        subtotal=calculated.subtotal,
        total=calculated.total,
    )

    inv = Invoice(
        user_id         = tmpl.user_id,
        invoice_number  = invoice_number,
        invoice_date    = scheduled_for.isoformat(),
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
        invoice_date_value=shadow_values["invoice_date_value"],
        due_date_value=shadow_values["due_date_value"],
        tax_rate_decimal=shadow_values["tax_rate_decimal"],
        discount_decimal=shadow_values["discount_decimal"],
        subtotal_decimal=shadow_values["subtotal_decimal"],
        total_decimal=shadow_values["total_decimal"],
        notes           = tmpl.notes,
        payment_info    = tmpl.payment_info,
        theme           = tmpl.theme or "default",
        status          = "draft",
        view_token      = secrets.token_urlsafe(32),
    )
    db.session.add(inv)
    db.session.flush()

    tmpl.last_run_date = scheduled_for
    tmpl.next_run_date = _next_run_date(scheduled_for, tmpl.interval)
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
