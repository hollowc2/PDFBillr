from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

from extensions import db
from models import (
    BrandingProfile,
    BusinessDefaults,
    Client,
    Estimate,
    Invoice,
    ServiceItem,
)


def _estimate_form(**overrides):
    today = date.today()
    values = {
        "estimate_number": "EST-001",
        "issue_date": today.isoformat(),
        "expiry_date": (today + timedelta(days=14)).isoformat(),
        "currency_code": "USD",
        "from_company": "Sender LLC",
        "from_address": "1 Sender Way",
        "from_email": "sender@example.test",
        "from_phone": "555-0100",
        "to_name": "Client Corp",
        "to_address": "2 Client Street",
        "to_email": "client@example.test",
        "description[]": ["Discovery", "Implementation"],
        "qty[]": ["1.25", "2"],
        "rate[]": ["80", "50"],
        "tax_rate": "10",
        "discount": "5",
        "notes": "Valid for the described scope.",
        "payment_info": "Net terms apply after conversion.",
    }
    values.update(overrides)
    return values


def _make_estimate(app, user_id: int, **overrides) -> int:
    today = date.today()
    defaults = {
        "user_id": user_id,
        "estimate_number": "EST-001",
        "public_token": "a" * 43,
        "status": "draft",
        "issue_date": today,
        "expiry_date": today + timedelta(days=14),
        "currency_code": "USD",
        "from_company": "Sender LLC",
        "to_name": "Client Corp",
        "line_items_json": json.dumps(
            [
                {
                    "description": "Work",
                    "qty": 2.0,
                    "rate": 50.0,
                    "amount": 100.0,
                }
            ]
        ),
        "tax_rate": Decimal("10"),
        "discount": Decimal("5"),
        "subtotal": Decimal("100"),
        "total": Decimal("105"),
    }
    defaults.update(overrides)
    with app.app_context():
        estimate = Estimate(**defaults)
        db.session.add(estimate)
        db.session.commit()
        return estimate.id


def test_estimate_routes_require_login(client):
    for path in ("/estimates/", "/estimates/new"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


def test_new_form_reuses_business_defaults_and_saved_services(
    app,
    client,
    make_user,
    login,
):
    owner = make_user("catalog@example.test")
    with app.app_context():
        defaults = BusinessDefaults(
            user_id=owner.id,
            from_company="Default Studio",
            default_tax_rate=Decimal("7.5"),
        )
        service = ServiceItem(
            user_id=owner.id,
            name="Strategy",
            normalized_name="strategy",
            description="Strategy workshop",
            default_rate=Decimal("250"),
            default_quantity=Decimal("1"),
        )
        db.session.add_all([defaults, service])
        db.session.commit()
        service_id = service.id
    login(owner.email)

    response = client.get(f"/estimates/new?service_id={service_id}")

    assert response.status_code == 200
    assert b"Default Studio" in response.data
    assert b"Strategy workshop" in response.data
    assert b'value="7.5000"' in response.data


def test_create_uses_authoritative_decimal_calculation_and_strong_token(
    app,
    client,
    make_user,
    login,
):
    user = make_user("owner@example.test")
    login(user.email)

    response = client.post("/estimates/new", data=_estimate_form())

    assert response.status_code == 302
    with app.app_context():
        estimate = Estimate.query.filter_by(user_id=user.id).one()
        assert estimate.subtotal == Decimal("200.00")
        assert estimate.tax_rate == Decimal("10.0000")
        assert estimate.discount == Decimal("5.00")
        assert estimate.total == Decimal("215.00")
        assert len(estimate.public_token) >= 43
        assert estimate.status == "draft"
        items = json.loads(estimate.line_items_json)
        assert items[0]["amount"] == 100.0
        assert items[1]["amount"] == 100.0


def test_estimate_number_is_unique_per_owner_but_reusable_between_owners(
    app,
    client,
    make_user,
    login,
):
    first = make_user("first@example.test")
    second = make_user("second@example.test")
    login(first.email)
    assert client.post("/estimates/new", data=_estimate_form()).status_code == 302
    duplicate = client.post("/estimates/new", data=_estimate_form())
    assert duplicate.status_code == 400

    client.get("/auth/logout")
    login(second.email)
    assert client.post("/estimates/new", data=_estimate_form()).status_code == 302
    with app.app_context():
        assert Estimate.query.filter_by(estimate_number="EST-001").count() == 2


def test_saved_client_reuse_is_owner_scoped_and_snapshotted(
    app,
    client,
    make_user,
    login,
):
    owner = make_user("owner@example.test")
    stranger = make_user("stranger@example.test")
    with app.app_context():
        client_record = Client(
            user_id=owner.id,
            name="Saved Client",
            normalized_name="saved client",
            email="saved@example.test",
            address="Saved address",
        )
        stranger_client = Client(
            user_id=stranger.id,
            name="Other Client",
            normalized_name="other client",
        )
        db.session.add_all([client_record, stranger_client])
        db.session.commit()
        client_id = client_record.id
        stranger_client_id = stranger_client.id

    login(owner.email)
    values = _estimate_form(
        client_id=str(client_id),
        to_name="",
        to_email="",
        to_address="",
    )
    assert client.post("/estimates/new", data=values).status_code == 302
    with app.app_context():
        estimate = Estimate.query.filter_by(user_id=owner.id).one()
        assert estimate.client_id == client_id
        assert estimate.to_name == "Saved Client"
        assert estimate.to_email == "saved@example.test"
        assert estimate.to_address == "Saved address"

    forbidden = client.post(
        "/estimates/new",
        data=_estimate_form(
            estimate_number="EST-002",
            client_id=str(stranger_client_id),
        ),
    )
    assert forbidden.status_code == 404


def test_owner_routes_do_not_expose_another_users_estimate(
    app,
    client,
    make_user,
    login,
):
    owner = make_user("owner@example.test")
    stranger = make_user("stranger@example.test")
    estimate_id = _make_estimate(app, owner.id)
    login(stranger.email)

    assert client.get(f"/estimates/{estimate_id}").status_code == 404
    assert client.get(f"/estimates/{estimate_id}/edit").status_code == 404
    assert client.post(f"/estimates/{estimate_id}/send").status_code == 404
    assert client.post(f"/estimates/{estimate_id}/convert").status_code == 404


def test_public_link_is_hidden_for_drafts_then_accepts_once(
    app,
    client,
    make_user,
    login,
):
    owner = make_user("owner@example.test")
    token = "p" * 43
    estimate_id = _make_estimate(app, owner.id, public_token=token)
    assert client.get(f"/estimates/view/{token}").status_code == 404

    login(owner.email)
    assert client.post(f"/estimates/{estimate_id}/send").status_code == 302
    client.get("/auth/logout")
    public_page = client.get(f"/estimates/view/{token}")
    assert public_page.status_code == 200
    assert b"Accept estimate" in public_page.data

    accepted = client.post(
        f"/estimates/view/{token}/accept",
        data={"client_comment": "<script>great scope</script>"},
        follow_redirects=True,
    )
    assert accepted.status_code == 200
    assert b"Accepted." in accepted.data
    assert b"&lt;script&gt;great scope&lt;/script&gt;" not in accepted.data
    # The public template intentionally shows the response state without
    # echoing the comment; the owner detail page is where it is displayed.
    second_response = client.post(
        f"/estimates/view/{token}/decline",
        data={"client_comment": "changed mind"},
    )
    assert second_response.status_code == 302
    with app.app_context():
        estimate = db.session.get(Estimate, estimate_id)
        assert estimate.status == "accepted"
        assert estimate.client_comment == "<script>great scope</script>"
        assert estimate.responded_at is not None

    login(owner.email)
    owner_page = client.get(f"/estimates/{estimate_id}")
    assert b"&lt;script&gt;great scope&lt;/script&gt;" in owner_page.data
    assert b"<script>great scope</script>" not in owner_page.data


def test_expired_and_terminal_estimates_are_immutable(
    app,
    client,
    make_user,
    login,
):
    owner = make_user("owner@example.test")
    expired_token = "e" * 43
    expired_id = _make_estimate(
        app,
        owner.id,
        public_token=expired_token,
        status="sent",
        issue_date=date.today() - timedelta(days=10),
        expiry_date=date.today() - timedelta(days=1),
    )
    response = client.post(f"/estimates/view/{expired_token}/accept")
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Estimate, expired_id).status == "expired"

    login(owner.email)
    edit_response = client.post(
        f"/estimates/{expired_id}/edit",
        data=_estimate_form(estimate_number="MUTATED"),
    )
    assert edit_response.status_code == 302
    with app.app_context():
        estimate = db.session.get(Estimate, expired_id)
        assert estimate.estimate_number == "EST-001"


def test_only_accepted_estimate_converts_and_conversion_is_idempotent(
    app,
    client,
    make_user,
    make_invoice,
    login,
):
    owner = make_user("owner@example.test")
    # Force a collision with the natural EST-001 -> INV-001 mapping.
    make_invoice(owner.id, invoice_number="INV-001")
    payment_url = "https://pay.example.com/accepted-estimate"
    with app.app_context():
        db.session.add(
            BusinessDefaults(
                user_id=owner.id,
                default_payment_url=payment_url,
            )
        )
        # A free account can retain stale branding metadata after a downgrade;
        # conversion must still enforce the current Pro entitlement.
        db.session.add(
            BrandingProfile(
                user_id=owner.id,
                logo_filename="stale-logo.png",
            )
        )
        db.session.commit()
    estimate_id = _make_estimate(
        app,
        owner.id,
        status="accepted",
        currency_code="EUR",
        subtotal=Decimal("999.00"),
        total=Decimal("999.00"),
        notes="Converted snapshot",
        payment_info="Bank details",
    )
    login(owner.email)

    converted = client.post(f"/estimates/{estimate_id}/convert")

    assert converted.status_code == 302
    with app.app_context():
        estimate = db.session.get(Estimate, estimate_id)
        invoice = db.session.get(Invoice, estimate.converted_invoice_id)
        assert estimate.status == "converted"
        assert invoice.invoice_number == "INV-001-2"
        assert invoice.status == "draft"
        assert invoice.currency_code == "EUR"
        # Conversion recalculates from canonical inputs instead of trusting
        # deliberately corrupted stored aggregate totals.
        assert invoice.subtotal_decimal == Decimal("100.00")
        assert invoice.total_decimal == Decimal("105.00")
        assert invoice.subtotal == 100.0
        assert invoice.total == 105.0
        assert invoice.invoice_date_value == date.today()
        assert invoice.due_date_value == date.today() + timedelta(days=30)
        assert invoice.notes == "Converted snapshot"
        assert invoice.payment_info == "Bank details"
        assert invoice.payment_url == payment_url
        assert invoice.logo_filename is None
        converted_invoice_id = invoice.id

    again = client.post(f"/estimates/{estimate_id}/convert")
    assert again.status_code == 302
    assert again.headers["Location"].endswith(
        f"/dashboard/invoice/{converted_invoice_id}"
    )
    with app.app_context():
        assert Invoice.query.filter_by(user_id=owner.id).count() == 2


def test_unaccepted_estimate_cannot_convert(
    app,
    client,
    make_user,
    login,
):
    owner = make_user("owner@example.test")
    estimate_id = _make_estimate(app, owner.id, status="sent")
    login(owner.email)

    response = client.post(f"/estimates/{estimate_id}/convert")

    assert response.status_code == 302
    with app.app_context():
        estimate = db.session.get(Estimate, estimate_id)
        assert estimate.converted_invoice_id is None
        assert Invoice.query.filter_by(user_id=owner.id).count() == 0
