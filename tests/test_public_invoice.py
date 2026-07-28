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


def test_authenticated_pages_are_not_cacheable(
    client, make_user, login
):
    user = make_user("person@example.test")
    login(user.email)
    response = client.get("/dashboard/")
    assert response.headers["Cache-Control"] == "no-store, private"
