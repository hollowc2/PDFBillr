import json
import logging
import time
import uuid
import warnings
from urllib.parse import urlsplit

import click
from flask import Flask, g, render_template, request
from flask_login import current_user
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config, _INSECURE_DEFAULT_SECRET
from extensions import csrf, db, limiter, login_manager, mail, migrate

log = logging.getLogger(__name__)


def _validate_config(app: Flask) -> None:
    """Reject security-critical production misconfiguration."""
    environment = str(app.config.get("APP_ENV", "development")).lower()
    secret = app.config.get("SECRET_KEY")
    insecure_secret = (
        not isinstance(secret, str)
        or len(secret) < 32
        or secret == _INSECURE_DEFAULT_SECRET
    )

    if insecure_secret:
        message = "SECRET_KEY must be set to a unique value of at least 32 characters."
        if environment == "production":
            raise RuntimeError(message)
        warnings.warn(f"{message} Using a development-only fallback.", stacklevel=2)

    public_base_url = app.config.get("PUBLIC_BASE_URL", "")
    if public_base_url:
        parsed = urlsplit(public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise RuntimeError(
                "PUBLIC_BASE_URL must be an absolute http(s) URL without a query or fragment."
            )
        if environment == "production" and parsed.scheme != "https":
            raise RuntimeError("PUBLIC_BASE_URL must use https in production.")
    elif environment == "production":
        raise RuntimeError("PUBLIC_BASE_URL is required in production.")

    if environment == "production" and not app.config.get("TRUSTED_HOSTS"):
        raise RuntimeError("TRUSTED_HOSTS is required in production.")

    stripe_values = {
        "STRIPE_SECRET_KEY": app.config.get("STRIPE_SECRET_KEY"),
        "STRIPE_PRICE_ID_PRO": app.config.get("STRIPE_PRICE_ID_PRO"),
        "STRIPE_WEBHOOK_SECRET": app.config.get("STRIPE_WEBHOOK_SECRET"),
    }
    if environment == "production" and any(stripe_values.values()):
        missing = [name for name, value in stripe_values.items() if not value]
        if missing:
            raise RuntimeError(
                "Partial Stripe configuration; missing " + ", ".join(missing)
            )

    limiter_storage = str(
        app.config.get("RATELIMIT_STORAGE_URI", "memory://")
    ).strip().lower()
    web_concurrency = int(app.config.get("WEB_CONCURRENCY", 1))
    if limiter_storage.startswith("memory://") and (
        environment == "production" or web_concurrency > 1
    ):
        raise RuntimeError(
            "A shared RATELIMIT_STORAGE_URI is required in production "
            "or when WEB_CONCURRENCY is greater than one."
        )


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)
    _validate_config(app)

    if not app.config.get("STRIPE_WEBHOOK_SECRET"):
        warnings.warn("STRIPE_WEBHOOK_SECRET env var not set. Webhook signature verification will fail.", stacklevel=1)

    # Only trust forwarded headers when the deployment explicitly opts in and
    # prevents direct client access to this process.
    if app.config.get("TRUST_PROXY_HEADERS"):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=1,
            x_proto=1,
            x_host=1,
            x_prefix=1,
        )

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db, directory="migrations")
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access that page."
    login_manager.login_message_category = "info"
    mail.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)

    # Set Stripe API key once at startup
    import stripe as _stripe_module
    _stripe_module.api_key = app.config.get("STRIPE_SECRET_KEY", "")

    # Blueprints
    from blueprints.public import bp as public_bp
    from blueprints.auth import bp as auth_bp
    from blueprints.dashboard import bp as dashboard_bp
    from blueprints.billing import bp as billing_bp
    from blueprints.clients import bp as clients_bp
    from blueprints.estimates import bp as estimates_bp
    from blueprints.exports import bp as exports_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(estimates_bp)
    app.register_blueprint(exports_bp)

    def _run_database_bootstrap() -> None:
        from utils.database_migrations import bootstrap_database

        transition = bootstrap_database(db)
        click.echo(f"Database schema is at the Alembic head ({transition}).")

    @app.cli.command("db-bootstrap")
    def db_bootstrap_command():
        """Upgrade fresh, versioned, or verified unversioned databases."""
        _run_database_bootstrap()

    @app.cli.command("db-upgrade")
    def db_upgrade_command():
        """Deprecated compatibility alias for db-bootstrap."""
        click.echo(
            "Warning: db-upgrade is deprecated; use db-bootstrap.",
            err=True,
        )
        _run_database_bootstrap()

    @app.cli.command("financial-data-audit")
    def financial_data_audit_command():
        """Read-only preflight for the typed financial-data migration."""
        from utils.financial_data_audit import audit_legacy_financial_data

        result = audit_legacy_financial_data(db.engine)
        click.echo(json.dumps(result.as_dict(), sort_keys=True))
        if result.blocker_count:
            raise click.exceptions.Exit(1)

    # Development/test convenience only. Production runs the explicit command
    # once before starting web or scheduler processes.
    if app.config.get("AUTO_CREATE_DB"):
        with app.app_context():
            if app.testing:
                # Ephemeral test databases mirror current metadata directly;
                # migration behavior has its own fresh/legacy test matrix.
                db.create_all()
            else:
                _run_database_bootstrap()

    @app.before_request
    def _start_request():
        # Generate this value locally. Accepting a client-supplied identifier
        # would permit log injection and misleading cross-system correlation.
        g.request_id = uuid.uuid4().hex
        g.request_started_ns = time.monotonic_ns()

    def _wants_json_error() -> bool:
        return request.is_json or (
            request.accept_mimetypes.best == "application/json"
            and request.accept_mimetypes["application/json"]
            >= request.accept_mimetypes["text/html"]
        )

    @app.errorhandler(HTTPException)
    def _render_http_error(error: HTTPException):
        request_id = getattr(g, "request_id", uuid.uuid4().hex)
        response = error.get_response()
        if error.code and error.code >= 500:
            app.logger.error(
                "request_failed request_id=%s endpoint=%s status=%s",
                request_id,
                request.endpoint or "unmatched",
                error.code,
                exc_info=getattr(error, "original_exception", None) is not None,
            )

        if _wants_json_error():
            response.data = app.json.dumps(
                {
                    "error": error.name,
                    "request_id": request_id,
                    "status": error.code,
                }
            )
            response.content_type = "application/json"
        else:
            response.data = render_template(
                "error.html",
                error_name=error.name,
                home_path=f"{request.script_root}/",
                request_id=request_id,
                status_code=error.code,
            )
            response.content_type = "text/html; charset=utf-8"
        return response

    # Security headers
    @app.after_request
    def _add_security_headers(response):
        request_id = getattr(g, "request_id", uuid.uuid4().hex)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "object-src 'none'; "
            "base-uri 'self';"
            "form-action 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        if app.config.get("ENABLE_HSTS"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        endpoint = request.endpoint or ""
        if response.status_code >= 400 or (
            current_user.is_authenticated
            or endpoint.startswith(
                ("auth.", "dashboard.", "billing.", "clients.", "estimates.")
            )
            or endpoint == "public.invoice_view"
        ):
            response.headers["Cache-Control"] = "no-store, private"

        started_ns = getattr(g, "request_started_ns", None)
        duration_ms = (
            (time.monotonic_ns() - started_ns) / 1_000_000
            if started_ns is not None
            else 0.0
        )
        app.logger.info(
            "request_complete request_id=%s method=%s endpoint=%s "
            "status=%s duration_ms=%.2f",
            request_id,
            request.method,
            endpoint or "unmatched",
            response.status_code,
            duration_ms,
        )
        return response

    # Template context processor: inject is_pro() for all templates
    from utils.currency import (
        currency_input_step,
        currency_options,
        currency_symbol,
        format_currency,
    )
    from utils.gating import is_pro

    app.jinja_env.filters["money"] = format_currency
    app.jinja_env.globals["currency_input_step"] = currency_input_step
    app.jinja_env.globals["currency_symbol"] = currency_symbol

    @app.context_processor
    def inject_pro():
        return {
            "is_pro": is_pro,
            "currency_options": currency_options(),
        }

    return app


if __name__ == "__main__":
    create_app().run(debug=False, host="127.0.0.1", port=8000)
