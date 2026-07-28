import logging
import warnings
from urllib.parse import urlsplit

import click
from flask import Flask, request
from flask_login import current_user
from sqlalchemy import inspect
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config, _INSECURE_DEFAULT_SECRET
from extensions import csrf, db, limiter, login_manager, mail

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


def _upgrade_legacy_schema(db_obj) -> None:
    """Upgrade known pre-review schemas without hiding unexpected DDL errors.

    This is a compatibility bridge for existing installs. New schema changes
    should move to Alembic; production invokes this explicitly with
    ``flask --app app db-upgrade`` rather than from every web worker.
    """
    inspector = inspect(db_obj.engine)
    if "invoices" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("invoices")}
    dialect = db_obj.engine.dialect.name
    timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
    bool_default = "FALSE" if dialect == "postgresql" else "0"
    invoice_columns = {
        "view_token": "VARCHAR(64)",
        "viewed_at": timestamp_type,
        "view_count": "INTEGER NOT NULL DEFAULT 0",
        "reminder_3d_sent": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
        "reminder_0d_sent": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
        "reminder_7d_sent": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
    }

    with db_obj.engine.begin() as connection:
        for column, column_type in invoice_columns.items():
            if column not in existing:
                connection.execute(
                    db_obj.text(
                        f"ALTER TABLE invoices ADD COLUMN {column} {column_type}"
                    )
                )
        connection.execute(
            db_obj.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_invoices_view_token "
                "ON invoices (view_token)"
            )
        )

    inspector = inspect(db_obj.engine)
    if "subscriptions" in inspector.get_table_names():
        subscription_columns = {
            column["name"] for column in inspector.get_columns("subscriptions")
        }
        if "last_stripe_event_created" not in subscription_columns:
            with db_obj.engine.begin() as connection:
                connection.execute(
                    db_obj.text(
                        "ALTER TABLE subscriptions "
                        "ADD COLUMN last_stripe_event_created BIGINT"
                    )
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

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(billing_bp)

    @app.cli.command("db-upgrade")
    def db_upgrade_command():
        """Create the current schema and upgrade supported legacy installs."""
        db.create_all()
        _upgrade_legacy_schema(db)
        click.echo("Database schema is up to date.")

    # Development/test convenience only. Production runs the explicit command
    # once before starting web or scheduler processes.
    if app.config.get("AUTO_CREATE_DB"):
        with app.app_context():
            db.create_all()
            _upgrade_legacy_schema(db)

    # Security headers
    @app.after_request
    def _add_security_headers(response):
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
        if (
            current_user.is_authenticated
            or endpoint.startswith(("auth.", "dashboard.", "billing."))
            or endpoint == "public.invoice_view"
        ):
            response.headers["Cache-Control"] = "no-store, private"
        return response

    # Template context processor: inject is_pro() for all templates
    from utils.gating import is_pro
    @app.context_processor
    def inject_pro():
        return {"is_pro": is_pro}

    return app


if __name__ == "__main__":
    create_app().run(debug=False, host="127.0.0.1", port=8000)
