import pytest
from utils.invoice_numbers import suggest_invoice_number


@pytest.mark.parametrize(
    ("number", "expected"),
    [(None, "INV-001"), ("INV-004", "INV-005"), ("2026-099", "2026-100"), ("CUSTOM", "INV-001")],
)
def test_suggestions(app, client, login, make_user, make_invoice, number, expected):
    user = make_user("number@example.test")
    if number:
        make_invoice(user.id, invoice_number=number)
    with app.app_context():
        assert suggest_invoice_number(user.id) == expected
    if number == "INV-004":
        login(user.email)
        assert b'value="INV-005"' in client.get("/app").data


def test_collision_and_route(app, client, login, make_user, make_invoice):
    user = make_user("collision@example.test")
    make_invoice(user.id, invoice_number="INV-005")
    make_invoice(user.id, invoice_number="INV-004")
    with app.app_context():
        assert suggest_invoice_number(user.id) == "INV-005-2"
    login(user.email)
    assert b'value="INV-005-2"' in client.get("/app").data
