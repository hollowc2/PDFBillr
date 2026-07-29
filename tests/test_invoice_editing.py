from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from extensions import db
from models import BrandingProfile, Invoice, InvoicePayment


def _edit_payload(invoice_number: str = "INV-EDITED", **overrides):
    data = {
        "invoice_number": invoice_number,
        "invoice_date": "2026-07-28",
        "due_date": "2026-08-27",
        "from_company": "Revised Studio",
        "from_address": "100 Main Street",
        "from_email": "billing@example.test",
        "from_phone": "555-0100",
        "to_name": "Updated Client",
        "to_address": "200 Client Avenue",
        "to_email": "accounts@example.test",
        "description[]": ["Design", "Hosting"],
        "qty[]": ["3", "1"],
        "rate[]": ["0.335", "0.005"],
        "tax_rate": "7.25",
        "discount": "0.01",
        "notes": "Revised scope",
        "payment_info": "ACH preferred",
        "theme": "default",
    }
    data.update(overrides)
    return data


def test_owner_can_open_prefilled_edit_form(
    client, make_user, make_invoice, login
):
    owner = make_user("owner@example.test", pro=True)
    invoice = make_invoice(
        owner.id,
        invoice_number="ORIGINAL-7",
        from_company="Original Studio",
        to_name="Original Client",
        invoice_date="2026-07-01",
        due_date="2026-07-31",
        line_items_json=json.dumps(
            [
                {
                    "description": "Consulting",
                    "qty": 2,
                    "rate": 55.5,
                    "amount": 111,
                }
            ]
        ),
        tax_rate=4.5,
        discount=1.25,
        theme="creative",
    )
    login(owner.email)

    response = client.get(f"/dashboard/invoice/{invoice.id}/edit")

    assert response.status_code == 200
    assert b"Edit Invoice ORIGINAL-7" in response.data
    assert b'value="Original Studio"' in response.data
    assert b'value="Original Client"' in response.data
    assert b"Consulting" in response.data
    assert b'value="creative"' in response.data
    assert b"Save Changes" in response.data


def test_edit_recalculates_authoritative_totals_and_shadow_values(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, invoice_number="ORIGINAL")
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload(),
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/dashboard/invoice/{invoice.id}"
    )
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.invoice_number == "INV-EDITED"
        assert stored.to_name == "Updated Client"
        assert json.loads(stored.line_items_json) == [
            {"description": "Design", "qty": 3.0, "rate": 0.335, "amount": 1.01},
            {"description": "Hosting", "qty": 1.0, "rate": 0.005, "amount": 0.01},
        ]
        assert stored.subtotal == 1.02
        assert stored.tax_rate == 7.25
        assert stored.discount == 0.01
        assert stored.total == 1.08
        assert stored.subtotal_decimal == Decimal("1.02")
        assert stored.total_decimal == Decimal("1.08")
        assert stored.invoice_date_value.isoformat() == "2026-07-28"
        assert stored.due_date_value.isoformat() == "2026-08-27"


def test_edit_rejects_duplicate_number_without_mutating_invoice(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(
        owner.id,
        invoice_number="KEEP-ME",
        to_name="Original Client",
    )
    make_invoice(owner.id, invoice_number="ALREADY-USED")
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload("ALREADY-USED"),
    )

    assert response.status_code == 200
    assert b"invoice number is already in use" in response.data
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.invoice_number == "KEEP-ME"
        assert stored.to_name == "Original Client"
        assert stored.total == 10.0


@pytest.mark.parametrize("method", ["get", "post"])
def test_invoice_edit_denies_cross_user_access(
    client, make_user, make_invoice, login, method
):
    owner = make_user("owner@example.test")
    attacker = make_user("attacker@example.test", pro=True)
    invoice = make_invoice(owner.id)
    login(attacker.email)

    response = getattr(client, method)(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload() if method == "post" else None,
    )

    assert response.status_code == 404


@pytest.mark.parametrize("status", ["paid", "void"])
def test_paid_and_void_invoices_are_immutable(
    client, app, make_user, make_invoice, login, status
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(
        owner.id,
        invoice_number="LOCKED",
        to_name="Original Client",
        status=status,
    )
    login(owner.email)

    get_response = client.get(f"/dashboard/invoice/{invoice.id}/edit")
    post_response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload(),
    )

    assert get_response.status_code == 302
    assert post_response.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.invoice_number == "LOCKED"
        assert stored.to_name == "Original Client"
        assert stored.status == status


def test_edit_preserves_sent_status_and_delivery_metadata(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    sent_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    invoice = make_invoice(
        owner.id,
        invoice_number="SENT-1",
        status="sent",
        sent_at=sent_at,
        view_token="existing-public-token",
        due_date="2026-07-31",
        reminder_3d_sent=True,
        reminder_0d_sent=True,
        reminder_7d_sent=True,
    )
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload("SENT-REVISED"),
    )

    assert response.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "sent"
        assert stored.sent_at.replace(tzinfo=timezone.utc) == sent_at
        assert stored.view_token == "existing-public-token"
        assert stored.reminder_3d_sent is False
        assert stored.reminder_0d_sent is False
        assert stored.reminder_7d_sent is False


def test_zero_total_draft_remains_editable_draft(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, invoice_number="NO-CHARGE", status="draft")
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload(
            "NO-CHARGE-REVISED",
            **{
                "description[]": ["Complimentary follow-up"],
                "qty[]": ["1"],
                "rate[]": ["0"],
                "tax_rate": "0",
                "discount": "0",
            },
        ),
    )

    assert response.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.total_amount == Decimal("0.00")
        assert stored.status == "draft"


def test_partial_invoice_edit_rejects_total_below_recorded_payments(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(
        owner.id,
        invoice_number="PARTIAL-1",
        status="partial",
        total=100,
    )
    with app.app_context():
        db.session.add(
            InvoicePayment(
                invoice_id=invoice.id,
                amount=Decimal("60.00"),
                paid_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
                method="card",
            )
        )
        db.session.commit()
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload(
            "PARTIAL-EDIT",
            **{
                "description[]": ["Reduced scope"],
                "qty[]": ["1"],
                "rate[]": ["50"],
                "tax_rate": "0",
                "discount": "0",
            },
        ),
    )

    assert response.status_code == 200
    assert b"cannot be less than payments already recorded" in response.data
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.invoice_number == "PARTIAL-1"
        assert stored.total == 100
        assert stored.status == "partial"
        assert stored.amount_paid == Decimal("60.00")


def test_partial_invoice_edit_syncs_status_against_revised_total(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(
        owner.id,
        invoice_number="PARTIAL-2",
        status="partial",
        total=100,
    )
    with app.app_context():
        db.session.add(
            InvoicePayment(
                invoice_id=invoice.id,
                amount=Decimal("60.00"),
                paid_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
                method="card",
            )
        )
        db.session.commit()
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload(
            "PARTIAL-SETTLED",
            **{
                "description[]": ["Final scope"],
                "qty[]": ["1"],
                "rate[]": ["60"],
                "tax_rate": "0",
                "discount": "0",
            },
        ),
    )

    assert response.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.total_amount == Decimal("60.00")
        assert stored.amount_paid == Decimal("60.00")
        assert stored.balance_due == Decimal("0.00")
        assert stored.status == "paid"
        assert stored.paid_at is not None


def test_edit_applies_pro_theme_and_branding_gates(
    client, app, make_user, make_invoice, login
):
    free_user = make_user("free@example.test")
    invoice = make_invoice(
        free_user.id,
        invoice_number="FREE-1",
        theme="creative",
        logo_filename="old-logo.png",
    )
    with app.app_context():
        db.session.add(
            BrandingProfile(
                user_id=free_user.id,
                logo_filename="new-logo.png",
                accent_color="#ff00aa",
                remove_footer=True,
            )
        )
        db.session.commit()
    login(free_user.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload("FREE-EDIT", theme="creative"),
    )

    assert response.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.theme == "default"
        assert stored.logo_filename is None


def test_pro_edit_can_apply_selected_theme_and_current_logo(
    client, app, make_user, make_invoice, login
):
    pro_user = make_user("pro@example.test", pro=True)
    invoice = make_invoice(pro_user.id, invoice_number="PRO-1")
    with app.app_context():
        db.session.add(
            BrandingProfile(
                user_id=pro_user.id,
                logo_filename="current-logo.png",
                accent_color="#ff00aa",
            )
        )
        db.session.commit()
    login(pro_user.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_edit_payload("PRO-EDIT", theme="creative"),
    )

    assert response.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.theme == "creative"
        assert stored.logo_filename == "current-logo.png"
