from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from extensions import db
from models import BusinessDefaults, Client, Invoice, ServiceItem


def _client_payload(name="Northwind Studio", **overrides):
    payload = {
        "name": name,
        "email": "billing@northwind.example",
        "address": "42 Harbor Road\nPortland, OR",
        "default_tax_rate": "8.25",
        "default_payment_terms_days": "14",
    }
    payload.update(overrides)
    return payload


def _service_payload(name="Design retainer", **overrides):
    payload = {
        "name": name,
        "description": "Monthly product design retainer",
        "default_rate": "1250.00",
        "default_quantity": "1",
    }
    payload.update(overrides)
    return payload


def _invoice_payload(client_id, **overrides):
    payload = {
        "client_id": str(client_id),
        "invoice_number": "CATALOG-001",
        "currency_code": "USD",
        "invoice_date": "2026-07-28",
        "due_date": "2026-08-11",
        "from_company": "Original Business",
        "from_address": "1 Sender Street",
        "from_email": "sender@example.test",
        "from_phone": "555-0100",
        "to_name": "Northwind Studio",
        "to_address": "42 Harbor Road\nPortland, OR",
        "to_email": "billing@northwind.example",
        "description[]": ["Monthly product design retainer"],
        "qty[]": ["1"],
        "rate[]": ["1250.00"],
        "tax_rate": "8.25",
        "discount": "0",
        "notes": "Original terms",
        "payment_info": "ACH",
        "theme": "default",
        "action": "download",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/clients/"),
        ("get", "/clients/new"),
        ("post", "/clients/new"),
        ("get", "/clients/services/new"),
        ("post", "/clients/services/new"),
        ("get", "/clients/defaults"),
        ("post", "/clients/defaults"),
        ("get", "/clients/search"),
    ],
)
def test_catalog_routes_require_login(client, method, path):
    response = getattr(client, method)(path, data={})

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_client_crud_rejects_case_insensitive_duplicate(
    client, app, make_user, login
):
    owner = make_user("owner@example.test")
    login(owner.email)

    created = client.post("/clients/new", data=_client_payload())
    duplicate = client.post(
        "/clients/new",
        data=_client_payload("  NORTHWIND   STUDIO  "),
    )

    assert created.status_code == 302
    assert duplicate.status_code == 200
    assert b"client with that name already exists" in duplicate.data
    with app.app_context():
        saved = Client.query.filter_by(user_id=owner.id).one()
        assert saved.name == "Northwind Studio"
        assert saved.normalized_name == "northwind studio"
        assert saved.email == "billing@northwind.example"
        assert saved.default_tax_rate == Decimal("8.2500")
        assert saved.default_payment_terms_days == 14

    edited = client.post(
        f"/clients/{saved.id}/edit",
        data=_client_payload(
            "Northwind Labs",
            email="accounts@northwind.example",
        ),
    )
    assert edited.status_code == 302
    with app.app_context():
        updated = db.session.get(Client, saved.id)
        assert updated.name == "Northwind Labs"
        assert updated.email == "accounts@northwind.example"


def test_service_crud_validation_and_duplicate_handling(
    client, app, make_user, login
):
    owner = make_user("owner@example.test")
    login(owner.email)

    invalid = client.post(
        "/clients/services/new",
        data=_service_payload(default_rate="-1"),
    )
    created = client.post(
        "/clients/services/new",
        data=_service_payload(),
    )
    duplicate = client.post(
        "/clients/services/new",
        data=_service_payload("DESIGN RETAINER"),
    )

    assert invalid.status_code == 200
    assert b"Default rate must be between" in invalid.data
    assert created.status_code == 302
    assert duplicate.status_code == 200
    assert b"service item with that name already exists" in duplicate.data
    with app.app_context():
        service = ServiceItem.query.filter_by(user_id=owner.id).one()
        assert service.description == "Monthly product design retainer"
        assert service.default_rate == Decimal("1250.00")
        assert service.default_quantity == Decimal("1.0000")


def test_business_and_client_defaults_prefill_new_invoice(
    client, app, make_user, login
):
    owner = make_user("owner@example.test")
    login(owner.email)
    defaults_response = client.post(
        "/clients/defaults",
        data={
            "from_company": "Evergreen Design",
            "from_address": "1 Sender Street",
            "from_email": "billing@evergreen.example",
            "from_phone": "555-0110",
            "default_tax_rate": "5.5",
            "default_payment_terms_days": "30",
            "default_notes": "Thanks for your business",
            "default_payment_info": "Pay by ACH",
        },
    )
    client_response = client.post(
        "/clients/new",
        data=_client_payload(),
    )
    assert defaults_response.status_code == 302
    assert client_response.status_code == 302

    with app.app_context():
        saved_client = Client.query.filter_by(user_id=owner.id).one()
        client_id = saved_client.id
    response = client.get(f"/app?client_id={client_id}")

    expected_due = (date.today() + timedelta(days=14)).isoformat().encode()
    assert response.status_code == 200
    assert b'value="Evergreen Design"' in response.data
    assert b'value="Northwind Studio"' in response.data
    assert b"billing@northwind.example" in response.data
    assert b'value="8.25"' in response.data
    assert f'value="{expected_due.decode()}"'.encode() in response.data
    assert b"Thanks for your business" in response.data
    assert b"Pay by ACH" in response.data


def test_service_query_prefills_line_item_and_catalog_selector(
    client, app, make_user, login
):
    owner = make_user("owner@example.test")
    login(owner.email)
    client.post("/clients/services/new", data=_service_payload())
    with app.app_context():
        service = ServiceItem.query.filter_by(user_id=owner.id).one()
        service_id = service.id

    response = client.get(f"/app?service_id={service_id}")

    assert response.status_code == 200
    assert b"Monthly product design retainer" in response.data
    assert b'"rates": ["1250"]' in response.data
    assert b'id="service-item-select"' in response.data


def test_created_invoice_keeps_client_and_service_snapshots(
    client, app, make_user, login, monkeypatch
):
    owner = make_user("owner@example.test")
    login(owner.email)
    client.post("/clients/new", data=_client_payload())
    client.post("/clients/services/new", data=_service_payload())
    with app.app_context():
        saved_client = Client.query.filter_by(user_id=owner.id).one()
        saved_service = ServiceItem.query.filter_by(user_id=owner.id).one()
        client_id = saved_client.id
        service_id = saved_service.id

    monkeypatch.setattr(
        "blueprints.public.render_pdf",
        lambda *_args, **_kwargs: b"%PDF-test",
    )
    response = client.post(
        "/generate",
        data=_invoice_payload(client_id),
    )
    assert response.status_code == 200

    client.post(
        f"/clients/{client_id}/edit",
        data=_client_payload(
            "Renamed Client",
            address="99 New Address",
        ),
    )
    client.post(
        f"/clients/services/{service_id}/edit",
        data=_service_payload(
            "Design retainer v2",
            description="Changed future description",
            default_rate="1500",
        ),
    )

    with app.app_context():
        invoice = Invoice.query.filter_by(user_id=owner.id).one()
        assert invoice.client_id == client_id
        assert invoice.to_name == "Northwind Studio"
        assert invoice.to_address == "42 Harbor Road\nPortland, OR"
        assert invoice.tax_rate_decimal == Decimal("8.2500")
        assert json.loads(invoice.line_items_json) == [
            {
                "description": "Monthly product design retainer",
                "qty": 1.0,
                "rate": 1250.0,
                "amount": 1250.0,
            }
        ]

    deleted = client.post(f"/clients/{client_id}/delete")
    assert deleted.status_code == 302
    with app.app_context():
        invoice = Invoice.query.filter_by(user_id=owner.id).one()
        assert invoice.client_id is None
        assert invoice.to_name == "Northwind Studio"
        assert invoice.to_address == "42 Harbor Road\nPortland, OR"


def test_catalog_and_selected_records_are_owner_scoped(
    client, app, make_user, login
):
    owner = make_user("owner@example.test")
    attacker = make_user("attacker@example.test")
    with app.app_context():
        foreign_client = Client(
            user_id=owner.id,
            name="Secret Customer",
            normalized_name="secret customer",
            email="secret@example.test",
        )
        foreign_service = ServiceItem(
            user_id=owner.id,
            name="Secret Service",
            normalized_name="secret service",
            description="Confidential work",
            default_rate=10,
            default_quantity=1,
        )
        db.session.add_all([foreign_client, foreign_service])
        db.session.commit()
        foreign_client_id = foreign_client.id
        foreign_service_id = foreign_service.id

    login(attacker.email)

    assert client.get(f"/clients/{foreign_client_id}/edit").status_code == 404
    assert (
        client.post(f"/clients/{foreign_client_id}/delete").status_code == 404
    )
    assert (
        client.get(f"/clients/services/{foreign_service_id}/edit").status_code
        == 404
    )
    assert (
        client.post(
            f"/clients/services/{foreign_service_id}/delete"
        ).status_code
        == 404
    )
    assert client.get(f"/app?client_id={foreign_client_id}").status_code == 404
    assert client.get(f"/app?service_id={foreign_service_id}").status_code == 404
    search = client.get("/clients/search?q=Secret")
    assert search.get_json() == {"clients": []}
    with app.app_context():
        assert db.session.get(Client, foreign_client_id) is not None
        assert db.session.get(ServiceItem, foreign_service_id) is not None


def test_invoice_creation_rejects_foreign_client_before_rendering(
    client, app, make_user, login, monkeypatch
):
    owner = make_user("owner@example.test")
    attacker = make_user("attacker@example.test")
    with app.app_context():
        foreign_client = Client(
            user_id=owner.id,
            name="Owner Client",
            normalized_name="owner client",
        )
        db.session.add(foreign_client)
        db.session.commit()
        foreign_client_id = foreign_client.id

    login(attacker.email)
    render_calls = []
    monkeypatch.setattr(
        "blueprints.public.render_pdf",
        lambda *_args, **_kwargs: render_calls.append(True) or b"%PDF-test",
    )

    response = client.post(
        "/generate",
        data=_invoice_payload(foreign_client_id),
    )

    assert response.status_code == 404
    assert render_calls == []
    with app.app_context():
        assert Invoice.query.filter_by(user_id=attacker.id).count() == 0


def test_same_normalized_names_are_allowed_for_different_owners(
    client, app, make_user, login
):
    first = make_user("first@example.test")
    second = make_user("second@example.test")
    login(first.email)
    assert client.post("/clients/new", data=_client_payload()).status_code == 302
    client.get("/auth/logout")
    login(second.email)
    assert client.post("/clients/new", data=_client_payload()).status_code == 302

    with app.app_context():
        assert Client.query.filter_by(normalized_name="northwind studio").count() == 2
        assert {
            saved.user_id
            for saved in Client.query.filter_by(
                normalized_name="northwind studio"
            ).all()
        } == {first.id, second.id}


def test_invalid_defaults_do_not_create_partial_record(
    client, app, make_user, login
):
    owner = make_user("owner@example.test")
    login(owner.email)

    response = client.post(
        "/clients/defaults",
        data={
            "from_company": "Studio",
            "from_email": "not-an-email",
            "default_tax_rate": "5",
            "default_payment_terms_days": "30",
        },
    )

    assert response.status_code == 200
    assert b"valid business email" in response.data
    with app.app_context():
        assert BusinessDefaults.query.filter_by(user_id=owner.id).first() is None
