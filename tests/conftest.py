from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import create_app
from extensions import db
from models import Invoice, RecurringInvoice, Subscription, User


@pytest.fixture()
def app(tmp_path: Path):
    database_path = tmp_path / "test.db"
    upload_path = tmp_path / "uploads"

    class TestConfig:
        TESTING = True
        APP_ENV = "test"
        SECRET_KEY = "tests-only-deterministic-secret-key-000000"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False
        MAIL_SUPPRESS_SEND = True
        MAIL_DEFAULT_SENDER = "test@example.invalid"
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        SESSION_COOKIE_SECURE = True
        SESSION_COOKIE_HTTPONLY = True
        SESSION_COOKIE_SAMESITE = "Lax"
        REMEMBER_COOKIE_SECURE = True
        REMEMBER_COOKIE_HTTPONLY = True
        REMEMBER_COOKIE_SAMESITE = "Lax"
        DISABLE_SCHEDULER = True
        STRIPE_SECRET_KEY = "sk_test_not_real"
        STRIPE_PUBLISHABLE_KEY = "pk_test_not_real"
        STRIPE_WEBHOOK_SECRET = "whsec_test_not_real"
        STRIPE_PRICE_ID_PRO = "price_pro"
        PUBLIC_BASE_URL = "https://billing.example.test/pdfbillr"
        TRUST_PROXY_HEADERS = False
        TRUSTED_HOSTS = ["localhost", "billing.example.test"]
        ENABLE_HSTS = False
        AUTO_CREATE_DB = True
        SCHEDULER_TIMEZONE = "UTC"
        UPLOAD_FOLDER = str(upload_path)
        MAX_LOGO_PIXELS = 10_000_000
        MAX_LOGO_DIMENSION = 4096
        MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    application = create_app(TestConfig)
    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_user(app):
    def factory(email: str, password: str = "correct horse", *, pro: bool = False):
        with app.app_context():
            user = User(email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            if pro:
                db.session.add(
                    Subscription(
                        user_id=user.id,
                        plan="pro",
                        status="active",
                        stripe_price_id="price_pro",
                    )
                )
            db.session.commit()
            return SimpleNamespace(id=user.id, email=user.email)

    return factory


@pytest.fixture()
def login(client):
    def perform(email: str, password: str = "correct horse", *, remember: bool = False):
        data = {"email": email, "password": password}
        if remember:
            data["remember"] = "1"
        return client.post("/auth/login", data=data)

    return perform


@pytest.fixture()
def make_invoice(app):
    def factory(user_id: int, **overrides):
        defaults = {
            "user_id": user_id,
            "invoice_number": "INV-001",
            "line_items_json": '[{"description":"Work","qty":1,"rate":10,"amount":10}]',
            "subtotal": 10.0,
            "tax_rate": 0.0,
            "discount": 0.0,
            "total": 10.0,
            "status": "draft",
        }
        defaults.update(overrides)
        with app.app_context():
            invoice = Invoice(**defaults)
            db.session.add(invoice)
            db.session.commit()
            return SimpleNamespace(id=invoice.id)

    return factory


@pytest.fixture()
def make_recurring(app):
    def factory(user_id: int, **overrides):
        from datetime import date

        defaults = {
            "user_id": user_id,
            "next_run_date": date.today(),
            "line_items_json": '[{"description":"Work","qty":1,"rate":10,"amount":10}]',
        }
        defaults.update(overrides)
        with app.app_context():
            recurring = RecurringInvoice(**defaults)
            db.session.add(recurring)
            db.session.commit()
            return SimpleNamespace(id=recurring.id)

    return factory
