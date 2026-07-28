from __future__ import annotations

import importlib
import re
from types import SimpleNamespace

import pytest
from flask import Flask, abort

from app import _validate_config, create_app


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


def test_production_requires_shared_rate_limit_storage():
    production_app = Flask(__name__)
    production_app.config.update(
        APP_ENV="production",
        SECRET_KEY="a-secure-production-secret-value-000000",
        PUBLIC_BASE_URL="https://billing.example.test",
        TRUSTED_HOSTS=["billing.example.test"],
        RATELIMIT_STORAGE_URI="memory://",
        WEB_CONCURRENCY=1,
    )

    with pytest.raises(RuntimeError, match="shared RATELIMIT_STORAGE_URI"):
        _validate_config(production_app)

    production_app.config["RATELIMIT_STORAGE_URI"] = (
        "redis://cache.internal:6379/0"
    )
    _validate_config(production_app)


def test_multiple_workers_require_shared_rate_limit_storage():
    development_app = Flask(__name__)
    development_app.config.update(
        APP_ENV="development",
        SECRET_KEY="a-secure-development-secret-value-000000",
        RATELIMIT_STORAGE_URI="memory://",
        WEB_CONCURRENCY=2,
    )

    with pytest.raises(RuntimeError, match="shared RATELIMIT_STORAGE_URI"):
        _validate_config(development_app)


def test_security_headers_are_present(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "form-action 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Permissions-Policy"]


def test_request_ids_are_generated_locally_and_logged_without_url_paths(
    client, caplog
):
    supplied_id = "attacker-controlled"
    with caplog.at_level("INFO"):
        first = client.get(
            "/?secret=must-not-be-logged",
            headers={"X-Request-ID": supplied_id},
        )
        second = client.get("/")

    first_id = first.headers["X-Request-ID"]
    second_id = second.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", first_id)
    assert first_id != supplied_id
    assert second_id != first_id
    assert f"request_id={first_id}" in caplog.text
    assert "secret=must-not-be-logged" not in caplog.text


def test_error_responses_are_generic_correlated_and_not_cached(client):
    html_response = client.get("/does-not-exist")
    assert html_response.status_code == 404
    assert b"Request ID:" in html_response.data
    assert html_response.headers["X-Request-ID"].encode() in html_response.data
    assert html_response.headers["Cache-Control"] == "no-store, private"

    json_response = client.get(
        "/does-not-exist",
        headers={"Accept": "application/json"},
    )
    assert json_response.status_code == 404
    assert json_response.get_json() == {
        "error": "Not Found",
        "request_id": json_response.headers["X-Request-ID"],
        "status": 404,
    }


def test_internal_error_page_does_not_expose_exception(client, app, caplog):
    def fail():
        abort(500, description="private database diagnostics")

    app.add_url_rule("/test-internal-error", "test_internal_error", fail)

    with caplog.at_level("ERROR"):
        response = client.get("/test-internal-error")

    assert response.status_code == 500
    assert b"private database diagnostics" not in response.data
    assert response.headers["X-Request-ID"].encode() in response.data
    assert f"request_id={response.headers['X-Request-ID']}" in caplog.text


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
    assert response.get_json()["checks"]["rate_limiter"] is True


def test_readiness_checks_configured_shared_rate_limiter(
    client, app, monkeypatch
):
    app.config["RATELIMIT_STORAGE_URI"] = "redis://cache.example.test/0"
    monkeypatch.setattr(
        "blueprints.public.limiter._storage",
        SimpleNamespace(check=lambda: False),
    )

    response = client.get("/health", headers={"Accept": "application/json"})

    assert response.status_code == 503
    assert response.get_json()["checks"]["rate_limiter"] is False


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
