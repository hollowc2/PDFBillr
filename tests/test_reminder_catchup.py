from datetime import date, datetime, timedelta, timezone
import pytest
from extensions import mail
from models import InvoiceDelivery
from utils.scheduler import send_payment_reminders


@pytest.mark.parametrize(
    ("delta", "sent_days_ago", "expected"),
    [
        (2, 5, "due in 2 days"),
        (2, 0, None),
        (-8, 30, "overdue"),
        (-9, 30, "overdue"),
        (-10, 30, None),
        (3, 5, "due in 3 days"),
        (3, None, "due in 3 days"),
    ],
)
def test_reminder_windows(
    app, make_user, make_invoice, monkeypatch, delta, sent_days_ago, expected
):
    today = date(2026, 11, 22)
    monkeypatch.setattr("utils.scheduler._business_today", lambda app: today)
    user = make_user("catchup@example.test", pro=True)
    invoice = make_invoice(
        user.id,
        status="sent",
        to_email="client@example.test",
        due_date=(today + timedelta(days=delta)).isoformat(),
        sent_at=(
            datetime.combine(
                today - timedelta(days=sent_days_ago), datetime.min.time(), tzinfo=timezone.utc
            )
            if sent_days_ago is not None
            else None
        ),
    )
    with app.app_context(), mail.record_messages() as messages:
        send_payment_reminders(app)
        send_payment_reminders(app)
        assert len(messages) == (1 if expected else 0)
        deliveries = InvoiceDelivery.query.filter_by(invoice_id=invoice.id).all()
        assert len(deliveries) == (1 if expected else 0)
        if expected:
            assert deliveries[0].delivery_kind == ("reminder_3d" if delta > 0 else "reminder_7d")
            assert deliveries[0].status == "sent"
            assert expected in messages[0].subject.lower()
