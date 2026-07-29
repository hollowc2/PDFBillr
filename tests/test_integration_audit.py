from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from extensions import db
from models import BrandingProfile, Invoice, InvoiceDelivery, InvoicePayment


def _invoice_edit_payload(
    *,
    invoice_number: str = "INV-EDITED",
    currency_code: str = "USD",
    due_date: str = "2026-08-31",
) -> dict:
    return {
        "invoice_number": invoice_number,
        "currency_code": currency_code,
        "invoice_date": "2026-07-28",
        "due_date": due_date,
        "from_company": "Sender",
        "from_email": "sender@example.test",
        "to_name": "Client",
        "to_email": "client@example.test",
        "description[]": ["Service"],
        "qty[]": ["1"],
        "rate[]": ["100"],
        "tax_rate": "0",
        "discount": "0",
        "theme": "default",
    }


def _generate_payload(*, invoice_number: str = "INV-NEW") -> dict:
    return {
        **_invoice_edit_payload(invoice_number=invoice_number),
        "action": "download",
    }


def test_paid_invoice_currency_cannot_reinterpret_existing_payments(
    app,
    client,
    login,
    make_invoice,
    make_user,
):
    owner = make_user("currency-lock@example.test")
    invoice = make_invoice(
        owner.id,
        invoice_number="USD-PARTIAL",
        currency_code="USD",
        status="partial",
        sent_at=datetime.now(timezone.utc),
        total=100,
        total_decimal=Decimal("100.00"),
    )
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        stored.payments.append(InvoicePayment(amount=Decimal("25.00")))
        db.session.commit()
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_invoice_edit_payload(currency_code="EUR"),
    )

    assert response.status_code == 200
    assert b"Currency cannot be changed" in response.data
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.currency_code == "USD"
        assert stored.amount_paid == Decimal("25.00")
        assert stored.balance_due == Decimal("75.00")


def test_due_date_edit_removes_only_stale_reminder_deliveries(
    app,
    client,
    login,
    make_invoice,
    make_user,
):
    owner = make_user("reschedule@example.test")
    invoice = make_invoice(
        owner.id,
        invoice_number="RESCHEDULE",
        status="sent",
        sent_at=datetime.now(timezone.utc),
        due_date="2026-07-31",
        reminder_3d_sent=True,
        reminder_0d_sent=True,
        reminder_7d_sent=True,
    )
    with app.app_context():
        for delivery_kind in (
            "reminder_3d",
            "reminder_0d",
            "reminder_7d",
            "recurring_auto_send",
        ):
            db.session.add(
                InvoiceDelivery(
                    invoice_id=invoice.id,
                    delivery_kind=delivery_kind,
                    status="sent",
                )
            )
        db.session.commit()
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_invoice_edit_payload(due_date="2026-08-31"),
    )

    assert response.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.reminder_3d_sent is False
        assert stored.reminder_0d_sent is False
        assert stored.reminder_7d_sent is False
        kinds = {
            delivery.delivery_kind
            for delivery in InvoiceDelivery.query.filter_by(
                invoice_id=invoice.id
            )
        }
        assert kinds == {"recurring_auto_send"}


def test_manual_payment_date_round_trips_in_business_timezone(
    app,
    client,
    login,
    make_invoice,
    make_user,
    monkeypatch,
):
    business_day = date(2026, 7, 31)
    app.config["SCHEDULER_TIMEZONE"] = "America/Los_Angeles"
    monkeypatch.setattr(
        "blueprints.dashboard._dashboard_today",
        lambda: business_day,
    )
    owner = make_user("payment-date@example.test")
    invoice = make_invoice(owner.id, invoice_number="DATED", total=10)
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={
            "amount": "10.00",
            "method": "cash",
            "payment_date": business_day.isoformat(),
        },
    )
    detail = client.get(f"/dashboard/invoice/{invoice.id}")

    assert response.status_code == 302
    assert business_day.isoformat().encode() in detail.data
    with app.app_context():
        payment = InvoicePayment.query.filter_by(invoice_id=invoice.id).one()
        stored_time = payment.paid_at
        if stored_time.tzinfo is None:
            stored_time = stored_time.replace(tzinfo=timezone.utc)
        assert stored_time.astimezone(
            ZoneInfo("America/Los_Angeles")
        ).date() == business_day


def test_manual_payment_rejects_future_business_date(
    app,
    client,
    login,
    make_invoice,
    make_user,
    monkeypatch,
):
    business_day = date(2026, 7, 31)
    app.config["SCHEDULER_TIMEZONE"] = "America/Los_Angeles"
    monkeypatch.setattr(
        "blueprints.dashboard._dashboard_today",
        lambda: business_day,
    )
    owner = make_user("future-payment@example.test")
    invoice = make_invoice(owner.id, invoice_number="FUTURE", total=10)
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={
            "amount": "10.00",
            "method": "cash",
            "payment_date": (business_day + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 302
    with app.app_context():
        assert InvoicePayment.query.filter_by(invoice_id=invoice.id).count() == 0


def test_dashboard_zero_metrics_follow_account_invoice_currencies(
    client,
    login,
    make_invoice,
    make_user,
):
    owner = make_user("euro-dashboard@example.test")
    make_invoice(
        owner.id,
        invoice_number="EUR-DRAFT",
        currency_code="EUR",
        total=0,
    )
    login(owner.email)

    response = client.get("/dashboard/")

    assert response.status_code == 200
    assert response.data.count("€0.00".encode()) >= 4
    assert b"$0.00" not in response.data


def test_free_user_stale_branding_is_not_snapshotted_on_new_invoice(
    app,
    client,
    login,
    make_user,
    monkeypatch,
):
    owner = make_user("free-branding@example.test")
    with app.app_context():
        db.session.add(
            BrandingProfile(
                user_id=owner.id,
                logo_filename="stale-logo.png",
            )
        )
        db.session.commit()
    login(owner.email)
    monkeypatch.setattr("blueprints.public.render_pdf", lambda *_args, **_kwargs: b"%PDF")

    response = client.post(
        "/generate",
        data=_generate_payload(invoice_number="FREE-BRAND"),
    )

    assert response.status_code == 200
    with app.app_context():
        stored = Invoice.query.filter_by(
            user_id=owner.id,
            invoice_number="FREE-BRAND",
        ).one()
        assert stored.logo_filename is None


class _BoundaryDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime(2026, 7, 29, 0, 30, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)


def test_public_and_export_status_use_configured_business_date(
    app,
    client,
    login,
    make_invoice,
    make_user,
    monkeypatch,
):
    app.config["SCHEDULER_TIMEZONE"] = "America/Los_Angeles"
    monkeypatch.setattr("blueprints.public.datetime", _BoundaryDateTime)
    monkeypatch.setattr("blueprints.exports.datetime", _BoundaryDateTime)
    owner = make_user("business-status@example.test")
    token = "business-date-status"
    make_invoice(
        owner.id,
        invoice_number="BUSINESS-DATE",
        status="sent",
        due_date="2026-07-28",
        due_date_value=date(2026, 7, 28),
        view_token=token,
    )

    public_response = client.get(f"/invoice/view/{token}")
    login(owner.email)
    export_response = client.get("/dashboard/invoices.csv")
    rows = list(
        csv.DictReader(
            io.StringIO(export_response.data.decode("utf-8-sig"))
        )
    )

    assert public_response.status_code == 200
    assert b"Payment Due" in public_response.data
    assert b"Overdue" not in public_response.data
    assert rows[0]["Status"] == "sent"
