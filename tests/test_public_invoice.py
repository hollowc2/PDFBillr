from __future__ import annotations

from extensions import db
from models import Invoice


def test_public_invoice_token_access_and_tracking(
    client, app, make_user, make_invoice
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(
        owner.id,
        view_token="strong-public-token",
        to_name="<script>not executable</script>",
    )

    first = client.get("/invoice/view/strong-public-token")
    second = client.get("/invoice/view/strong-public-token")

    assert first.status_code == 200
    assert b"&lt;script&gt;not executable&lt;/script&gt;" in first.data
    assert first.headers["Cache-Control"] == "no-store, private"
    assert second.status_code == 200
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.view_count == 2
        assert stored.viewed_at is not None


def test_invalid_public_invoice_token_is_404(client):
    assert client.get("/invoice/view/not-a-token").status_code == 404


def test_owner_can_rotate_public_link_and_old_link_stops_working(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, view_token="old-public-token")
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/public-link/rotate"
    )

    assert response.status_code == 302
    assert client.get("/invoice/view/old-public-token").status_code == 404
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.view_token
        assert stored.view_token != "old-public-token"
        replacement_token = stored.view_token
    assert client.get(f"/invoice/view/{replacement_token}").status_code == 200


def test_owner_can_revoke_and_recreate_public_link(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, view_token="revoked-public-token")
    login(owner.email)

    revoke = client.post(
        f"/dashboard/invoice/{invoice.id}/public-link/revoke"
    )

    assert revoke.status_code == 302
    assert client.get("/invoice/view/revoked-public-token").status_code == 404
    with app.app_context():
        assert db.session.get(Invoice, invoice.id).view_token is None

    recreate = client.post(
        f"/dashboard/invoice/{invoice.id}/public-link/rotate"
    )
    assert recreate.status_code == 302
    with app.app_context():
        assert db.session.get(Invoice, invoice.id).view_token


def test_authenticated_pages_are_not_cacheable(
    client, make_user, login
):
    user = make_user("person@example.test")
    login(user.email)
    response = client.get("/dashboard/")
    assert response.headers["Cache-Control"] == "no-store, private"
