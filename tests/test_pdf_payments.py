import pytest
from decimal import Decimal
from flask import render_template
from extensions import db
from models import Invoice, InvoicePayment
from utils.pdf import context_from_invoice


@pytest.mark.parametrize(
    "theme", ["invoice", "invoice_minimal", "invoice_corporate", "invoice_creative"]
)
def test_pdf_payment_totals(app, make_user, make_invoice, theme):
    user = make_user("pdf-payment@example.test")
    item = make_invoice(user.id, total=100, subtotal=100)
    with app.test_request_context():
        inv = db.session.get(Invoice, item.id)
        html = render_template(theme + ".html", **context_from_invoice(inv))
        assert "Total due" in html
        assert "Balance due" not in html
        inv.payments.append(InvoicePayment(amount=Decimal("40.00"), method="cash"))
        db.session.commit()
        html = render_template(theme + ".html", **context_from_invoice(inv))
        assert "Balance due" in html
        assert "$60.00" in html
        assert "-$40.00" in html
        assert "Total due" not in html
