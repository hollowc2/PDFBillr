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


def test_free_branding_uses_defaults(app, make_user, make_invoice):
    from models import BrandingProfile
    from utils.branding import DEFAULT_ACCENT_COLOR

    user = make_user("free-brand@example.test")
    item = make_invoice(user.id)
    with app.app_context():
        db.session.add(BrandingProfile(user_id=user.id, accent_color="#abcdef", remove_footer=True))
        db.session.commit()
        context = context_from_invoice(db.session.get(Invoice, item.id))
        assert context["accent_color"] == DEFAULT_ACCENT_COLOR
        assert context["remove_footer"] is False
