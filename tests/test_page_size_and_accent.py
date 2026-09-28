"""Paper size per invoice and account, and the brand accent data migration."""

import re

import pytest
from sqlalchemy import inspect, text
from werkzeug.datastructures import MultiDict

from app import create_app
from extensions import db
from models import BusinessDefaults, Invoice
from utils.branding import normalize_page_size
from utils.pdf import _THEME_TEMPLATES, RestrictedURLFetcher, build_invoice_context

# Page size in PDF points (1/72 in): A4 is 595 x 842, US Letter is 612 x 792.
_POINTS = {"A4": (595, 842), "Letter": (612, 792)}


def _rendered_points(context: dict, theme: str) -> tuple[int, int]:
    from flask import render_template
    from weasyprint import HTML

    html = render_template(_THEME_TEMPLATES[theme], **context)
    page = HTML(string=html, url_fetcher=RestrictedURLFetcher()).render().pages[0]
    # WeasyPrint reports CSS pixels (1/96 in).
    return round(page.width * 72 / 96), round(page.height * 72 / 96)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("letter", "Letter"), (" LETTER ", "Letter"), ("A4", "A4"), ("", "A4"), ("legal", "A4"), (None, "A4")],
)
def test_page_size_input_is_normalized(raw, expected):
    assert normalize_page_size(raw) == expected


@pytest.mark.parametrize("theme", ["default", "minimal", "corporate", "creative"])
@pytest.mark.parametrize("page_size", ["A4", "Letter"])
def test_every_template_renders_the_requested_paper_size(app, theme, page_size):
    form = MultiDict(
        [("invoice_number", "P-1"), ("description[]", "Work"), ("qty[]", "1"), ("rate[]", "10"),
         ("page_size", page_size)]
    )
    with app.app_context(), app.test_request_context():
        assert _rendered_points(build_invoice_context(form), theme) == _POINTS[page_size]


def test_account_default_prefills_new_invoices_and_saved_invoices_keep_their_size(
    client, app, make_user, login, monkeypatch
):
    owner = make_user("owner@example.test")
    login(owner.email)

    saved = client.post(
        "/clients/defaults",
        data={"default_tax_rate": "0", "default_payment_terms_days": "30", "default_page_size": "Letter"},
    )
    assert saved.status_code == 302
    with app.app_context():
        assert db.session.query(BusinessDefaults).filter_by(user_id=owner.id).one().default_page_size == "Letter"

    form_page = client.get("/app").data.decode()
    assert re.search(r'<option value="Letter"\s+selected', form_page)

    client.post(
        "/dashboard/save-draft",
        data={"invoice_number": "LTR-1", "description[]": "Work", "qty[]": "1", "rate[]": "10",
              "page_size": "Letter"},
    )
    with app.app_context():
        invoice = Invoice.query.filter_by(invoice_number="LTR-1").one()
        assert invoice.page_size == "Letter"
        invoice_id = invoice.id

    client.post(f"/dashboard/invoice/{invoice_id}/duplicate")
    with app.app_context():
        copy = Invoice.query.filter(Invoice.invoice_number != "LTR-1").one()
        assert copy.page_size == "Letter"

    rendered = {}
    monkeypatch.setattr(
        "blueprints.dashboard.render_pdf",
        lambda context, **_kwargs: rendered.setdefault("page_size", context["page_size"]) and b"%PDF-test",
    )
    assert client.get(f"/dashboard/invoice/{invoice_id}/download").status_code == 200
    assert rendered["page_size"] == "Letter"


def _migration_app(tmp_path):
    class MigrationConfig:
        TESTING = True
        APP_ENV = "test"
        SECRET_KEY = "tests-only-deterministic-secret-key-000000"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'migrate.db'}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_CREATE_DB = False
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        WTF_CSRF_ENABLED = False
        PUBLIC_BASE_URL = "https://billing.example.test/pdfbillr"
        TRUST_PROXY_HEADERS = False
        TRUSTED_HOSTS = ["localhost"]
        STRIPE_WEBHOOK_SECRET = "whsec_test"

    application = create_app(MigrationConfig)
    return application, application.test_cli_runner()


def test_accent_migration_moves_only_the_old_default(tmp_path):
    application, runner = _migration_app(tmp_path)
    assert runner.invoke(args=["db", "upgrade", "20260728_11"]).exit_code == 0

    with application.app_context():
        for user_id, colour in [(1, "#1e3a8a"), (2, "#1E3A8A"), (3, "#7c3aed")]:
            db.session.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, auth_session_version, is_active) "
                    "VALUES (:id, :email, 'hash', 1, 1)"
                ),
                {"id": user_id, "email": f"u{user_id}@example.test"},
            )
            db.session.execute(
                text("INSERT INTO branding_profiles (user_id, accent_color) VALUES (:id, :colour)"),
                {"id": user_id, "colour": colour},
            )
        db.session.commit()

    def colours():
        with application.app_context():
            rows = db.session.execute(
                text("SELECT user_id, accent_color FROM branding_profiles ORDER BY user_id")
            )
            return [colour for _user, colour in rows]

    upgraded = runner.invoke(args=["db", "upgrade", "20260928_12"])
    assert upgraded.exit_code == 0, upgraded.output
    assert colours() == ["#2743A6", "#2743A6", "#7c3aed"]

    downgraded = runner.invoke(args=["db", "downgrade", "20260728_11"])
    assert downgraded.exit_code == 0, downgraded.output
    assert colours() == ["#1e3a8a", "#1e3a8a", "#7c3aed"]


def test_page_size_migration_is_additive_and_reversible(tmp_path):
    application, runner = _migration_app(tmp_path)
    assert runner.invoke(args=["db", "upgrade", "20260928_12"]).exit_code == 0

    with application.app_context():
        db.session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, auth_session_version, is_active) "
                "VALUES (1, 'legacy@example.test', 'hash', 1, 1)"
            )
        )
        db.session.execute(
            text("INSERT INTO invoices (id, user_id, invoice_number, currency_code) VALUES (1, 1, 'OLD', 'USD')")
        )
        db.session.commit()

    upgraded = runner.invoke(args=["db", "upgrade", "20260928_13"])
    assert upgraded.exit_code == 0, upgraded.output
    with application.app_context():
        assert db.session.execute(text("SELECT page_size FROM invoices WHERE id = 1")).scalar_one() == "A4"
        assert "default_page_size" in {c["name"] for c in inspect(db.engine).get_columns("business_defaults")}

    downgraded = runner.invoke(args=["db", "downgrade", "20260928_12"])
    assert downgraded.exit_code == 0, downgraded.output
    with application.app_context():
        assert "page_size" not in {c["name"] for c in inspect(db.engine).get_columns("invoices")}
