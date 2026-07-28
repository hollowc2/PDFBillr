from __future__ import annotations

from datetime import date, timedelta

from extensions import db
from models import Invoice
from scheduler_worker import create_scheduler
from utils.scheduler import process_recurring_invoices, send_payment_reminders


def test_dedicated_scheduler_has_stable_single_instance_jobs(app):
    scheduler = create_scheduler(app)
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert set(jobs) == {"payment-reminders", "recurring-invoices"}
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

    send_payment_reminders(app)

    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.reminder_3d_sent is True
        assert stored.view_token

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
