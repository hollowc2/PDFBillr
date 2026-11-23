from datetime import date, datetime, timedelta, timezone
import pytest
from extensions import mail
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
    ],
)
def test_reminder_windows(
    app, make_user, make_invoice, monkeypatch, delta, sent_days_ago, expected
):
    today = date(2026, 11, 22)
    monkeypatch.setattr("utils.scheduler._business_today", lambda app: today)
    user = make_user("catchup@example.test", pro=True)
    make_invoice(
        user.id,
        status="sent",
        to_email="client@example.test",
        due_date=(today + timedelta(days=delta)).isoformat(),
        sent_at=datetime.combine(
            today - timedelta(days=sent_days_ago), datetime.min.time(), tzinfo=timezone.utc
        ),
    )
    with app.app_context(), mail.record_messages() as messages:
        send_payment_reminders(app)
        send_payment_reminders(app)
        assert len(messages) == (1 if expected else 0)
        if expected:
            assert expected in messages[0].subject.lower()
