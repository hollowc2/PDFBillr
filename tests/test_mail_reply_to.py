"""Mail goes out from a no-reply sender, so anything inviting a reply sets Reply-To."""

import pytest

from extensions import mail


def _send_invoice(client, make_user, make_invoice, login, monkeypatch, **invoice_fields):
    owner = make_user("owner@example.test", pro=True)
    invoice = make_invoice(owner.id, to_email="client@example.test", **invoice_fields)
    login(owner.email)
    messages = []
    monkeypatch.setattr("blueprints.dashboard.render_pdf", lambda *_a, **_k: b"%PDF-test")
    monkeypatch.setattr("blueprints.dashboard.mail.send", messages.append)

    response = client.post(f"/dashboard/invoice/{invoice.id}/send")

    assert response.status_code == 302
    assert len(messages) == 1
    return messages[0]


def test_invoice_email_replies_go_to_the_invoice_sender(
    client, make_user, make_invoice, login, monkeypatch
):
    message = _send_invoice(
        client, make_user, make_invoice, login, monkeypatch, from_email="billing@studio.example"
    )
    assert message.reply_to == "billing@studio.example"


def test_invoice_email_replies_fall_back_to_the_account_email(
    client, make_user, make_invoice, login, monkeypatch
):
    message = _send_invoice(client, make_user, make_invoice, login, monkeypatch, from_email="")
    assert message.reply_to == "owner@example.test"


@pytest.mark.parametrize(
    ("support_email", "expected_reply_to", "invites_reply"),
    [("", None, False), ("help@pdfbillr.example", "help@pdfbillr.example", True)],
)
def test_account_emails_use_support_email_only_when_configured(
    client, app, support_email, expected_reply_to, invites_reply
):
    app.config["SUPPORT_EMAIL"] = support_email

    with app.app_context(), mail.record_messages() as outbox:
        response = client.post(
            "/auth/register",
            data={
                "email": "new@example.test",
                "password": "correct-horse-battery",
                "confirm_password": "correct-horse-battery",
            },
        )

    assert response.status_code == 302
    welcome = next(m for m in outbox if m.subject == "Welcome to PDFBillr")
    assert welcome.reply_to == expected_reply_to
    assert ("reply to this email" in welcome.body) is invites_reply


def test_error_page_shows_support_email_only_when_configured(client, app):
    assert b"help@pdfbillr.example" not in client.get("/does-not-exist-anywhere").data

    app.config["SUPPORT_EMAIL"] = "help@pdfbillr.example"
    response = client.get("/billing/webhook")  # GET on a POST-only route: 405

    assert response.status_code == 405
    assert b"help@pdfbillr.example" in response.data
