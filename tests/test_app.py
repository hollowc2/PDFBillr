from __future__ import annotations

import importlib

import pytest

from app import create_app


def test_importing_factory_does_not_construct_application():
    app_module = importlib.import_module("app")
    assert not hasattr(app_module, "app")


@pytest.mark.parametrize("secret", ["", "short", "dev-only-insecure-default-do-not-use-in-production"])
def test_production_rejects_insecure_secret(secret):
    class ProductionConfig:
        APP_ENV = "production"
        SECRET_KEY = secret
        PUBLIC_BASE_URL = "https://billing.example.test/pdfbillr"
        TRUSTED_HOSTS = ["billing.example.test"]

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(ProductionConfig)


def test_production_requires_canonical_url_and_trusted_hosts():
    class MissingOrigin:
        APP_ENV = "production"
        SECRET_KEY = "a-secure-production-secret-value-000000"
        PUBLIC_BASE_URL = ""
        TRUSTED_HOSTS = ["billing.example.test"]

    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        create_app(MissingOrigin)

    class MissingHosts:
        APP_ENV = "production"
        SECRET_KEY = "a-secure-production-secret-value-000000"
        PUBLIC_BASE_URL = "https://billing.example.test"
        TRUSTED_HOSTS = None

    with pytest.raises(RuntimeError, match="TRUSTED_HOSTS"):
        create_app(MissingHosts)


def test_production_rejects_partial_stripe_configuration():
    class PartialStripe:
        APP_ENV = "production"
        SECRET_KEY = "a-secure-production-secret-value-000000"
        PUBLIC_BASE_URL = "https://billing.example.test"
        TRUSTED_HOSTS = ["billing.example.test"]
        STRIPE_SECRET_KEY = "sk_live_configured"
        STRIPE_PRICE_ID_PRO = ""
        STRIPE_WEBHOOK_SECRET = ""

    with pytest.raises(RuntimeError, match="Partial Stripe"):
        create_app(PartialStripe)


def test_security_headers_are_present(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "form-action 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Permissions-Policy"]


def test_host_allowlist_rejects_unknown_host(client):
    response = client.get("/", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_forwarded_headers_are_not_trusted_by_default(client):
    response = client.get(
        "/",
        headers={
            "Host": "localhost",
            "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-For": "203.0.113.99",
        },
    )
    assert response.status_code == 200


def test_health_starts_with_scheduler_disabled(client):
    response = client.get("/health", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.get_json()["checks"]["database"] is True


def test_liveness_does_not_probe_dependencies(client, monkeypatch):
    monkeypatch.setattr(
        "blueprints.public.db.session.execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError),
    )
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_readiness_returns_503_and_rolls_back_on_database_failure(
    client, monkeypatch
):
    rolled_back = []

    def fail(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("blueprints.public.db.session.execute", fail)
    monkeypatch.setattr(
        "blueprints.public.db.session.rollback",
        lambda: rolled_back.append(True),
    )
    response = client.get("/health", headers={"Accept": "application/json"})

    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"
    assert rolled_back == [True]


def test_csrf_rejects_unprotected_post(client, app):
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post(
        "/auth/register",
        data={
            "email": "person@example.test",
            "password": "long password",
            "confirm_password": "long password",
        },
    )
    assert response.status_code == 400
