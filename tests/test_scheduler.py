from __future__ import annotations

from datetime import date, timedelta

from extensions import db
from models import Invoice, InvoiceDelivery, RecurringOccurrence
from scheduler_worker import create_scheduler
from utils.scheduler import (
    process_recurring_invoices,
    retry_invoice_deliveries,
    send_payment_reminders,
)


def test_dedicated_scheduler_has_stable_single_instance_jobs(app):
    scheduler = create_scheduler(app)
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert set(jobs) == {
        "billing-notification-retries",
        "invoice-delivery-retries",
        "payment-reminders",
        "recurring-invoices",
    }
    assert scheduler._job_defaults["max_instances"] == 1
    assert scheduler._job_defaults["coalesce"] is True


def test_reminder_mail_failure_leaves_flag_retryable(
    app, make_user, make_invoice, monkeypatch
):
    user = make_user("pro@example.test", pro=True)
    invoice = make_invoice(
        user.id,
        status="sent",
        due_date=(date.today() + timedelta(days=3)).isoformat(),
        to_email="client@example.test",
        view_token=None,
    )
    calls = []

    def fail_once(message):
        calls.append(message)
        if len(calls) == 1:
            raise OSError("temporary SMTP failure")

    monkeypatch.setattr("extensions.mail.send", fail_once)
    send_payment_reminders(app)

    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.reminder_3d_sent is False
        delivery = InvoiceDelivery.query.filter_by(
            invoice_id=invoice.id,
            delivery_kind="reminder_3d",
        ).one()
        assert delivery.status == "failed"
        assert delivery.attempt_count == 1
        # The durable queue must retry after the original trigger day.
        stored.due_date = (date.today() + timedelta(days=2)).isoformat()
        db.session.commit()

    retry_invoice_deliveries(app)

    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.reminder_3d_sent is True
        assert stored.view_token
        delivery = InvoiceDelivery.query.filter_by(
            invoice_id=invoice.id,
            delivery_kind="reminder_3d",
        ).one()
        assert delivery.status == "sent"
        assert delivery.attempt_count == 2

    assert len(calls) == 2
    assert "https://billing.example.test/pdfbillr/invoice/view/" in calls[1].body


def test_recurring_job_skips_non_pro_owner(
    app, make_user, make_recurring
):
    user = make_user("free@example.test")
    make_recurring(user.id, next_run_date=date.today())

    process_recurring_invoices(app)

    with app.app_context():
        assert Invoice.query.filter_by(user_id=user.id).count() == 0


def test_recurring_occurrence_prevents_duplicate_and_retries_auto_send(
    app, make_user, make_recurring, monkeypatch
):
    user = make_user("pro-recurring@example.test", pro=True)
    template = make_recurring(
        user.id,
        next_run_date=date.today(),
        auto_send=True,
        to_email="client@example.test",
    )
    calls = []

    def fail_once(message):
        calls.append(message)
        if len(calls) == 1:
            raise OSError("temporary SMTP failure")

    monkeypatch.setattr("extensions.mail.send", fail_once)
    monkeypatch.setattr("utils.pdf.render_pdf", lambda *_args, **_kwargs: b"%PDF")

    process_recurring_invoices(app)

    with app.app_context():
        invoices = Invoice.query.filter_by(user_id=user.id).all()
        assert len(invoices) == 1
        assert RecurringOccurrence.query.filter_by(
            recurring_invoice_id=template.id,
            scheduled_for=date.today(),
        ).count() == 1
        delivery = InvoiceDelivery.query.filter_by(
            invoice_id=invoices[0].id,
            delivery_kind="recurring_auto_send",
        ).one()
        assert delivery.status == "failed"
        assert delivery.attempt_count == 1

    process_recurring_invoices(app)

    with app.app_context():
        invoices = Invoice.query.filter_by(user_id=user.id).all()
        assert len(invoices) == 1
        delivery = InvoiceDelivery.query.filter_by(
            invoice_id=invoices[0].id,
            delivery_kind="recurring_auto_send",
        ).one()
        assert delivery.status == "sent"
        assert delivery.attempt_count == 2
        assert invoices[0].status == "sent"

    assert len(calls) == 2
