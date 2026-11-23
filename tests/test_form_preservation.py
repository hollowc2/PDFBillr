import json
from extensions import db
from models import RecurringInvoice


def recurring_payload(**extra):
    return {
        "interval": "monthly",
        "next_run_date": "2026-12-01",
        "net_days": "30",
        "line_items_json": json.dumps([{"description": "Work", "qty": 1, "rate": 10}]),
        **extra,
    }


def test_failed_draft_preserves_input(client, make_user, make_invoice, login):
    user = make_user("draft@example.test")
    make_invoice(user.id)
    login(user.email)
    response = client.post(
        "/dashboard/save-draft",
        data={
            "invoice_number": "INV-001",
            "to_name": "Submitted client",
            "description[]": "Work",
            "qty[]": "1",
            "rate[]": "10",
        },
    )
    assert response.status_code == 422
    assert b"Submitted client" in response.data


def test_failed_recurring_preserves_input(client, app, make_user, make_recurring, login):
    user = make_user("recurring@example.test", pro=True)
    login(user.email)
    response = client.post(
        "/dashboard/recurring/new",
        data=recurring_payload(to_email="invalid", from_company="Submitted studio"),
    )
    assert response.status_code == 422
    assert b"Submitted studio" in response.data
    item = make_recurring(user.id, to_name="Saved client")
    response = client.post(
        f"/dashboard/recurring/{item.id}/edit",
        data=recurring_payload(to_name="Submitted client", payment_url="javascript:alert(1)"),
    )
    assert response.status_code == 422
    assert b"Submitted client" in response.data
    with app.app_context():
        assert db.session.get(RecurringInvoice, item.id).to_name == "Saved client"


def test_recurring_empty_saved_fields_use_defaults(client, make_user, make_recurring, login):
    user = make_user("empty-recurring@example.test", pro=True)
    item = make_recurring(user.id, from_company=None, to_name=None)
    login(user.email)
    response = client.get(f"/dashboard/recurring/{item.id}/edit")
    assert response.status_code == 200
    assert b'value="None"' not in response.data
