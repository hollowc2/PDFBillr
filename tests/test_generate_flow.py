from models import Invoice


def payload(**extra):
    return {
        "invoice_number": "FLOW-001",
        "description[]": "Work",
        "qty[]": "1",
        "rate[]": "10",
        **extra,
    }


def test_preview_then_download(client, app, make_user, login, monkeypatch):
    monkeypatch.setattr("blueprints.public.render_pdf", lambda *a, **kw: b"%PDF-test")
    user = make_user("flow@example.test")
    login(user.email)
    preview = client.post("/generate", data=payload(action="preview"))
    assert preview.mimetype == "application/pdf"
    with app.app_context():
        assert Invoice.query.count() == 0
    download = client.post("/generate", data=payload(action="download"))
    assert download.mimetype == "application/pdf"
    with app.app_context():
        inv = Invoice.query.one()
        assert download.headers["X-Invoice-URL"] == f"/dashboard/invoice/{inv.id}"
    conflict = client.post("/generate", data=payload())
    assert conflict.status_code == 422
    assert conflict.mimetype == "text/html"


def test_anonymous_download_and_validation(client, app, monkeypatch):
    monkeypatch.setattr("blueprints.public.render_pdf", lambda *a, **kw: b"%PDF-test")
    response = client.post("/generate", data=payload())
    assert response.mimetype == "application/pdf"
    assert "X-Invoice-URL" not in response.headers
    with app.app_context():
        assert Invoice.query.count() == 0
    response = client.post("/generate", data=payload(payment_url="javascript:alert(1)"))
    assert response.status_code == 422
    assert response.mimetype == "text/html"
