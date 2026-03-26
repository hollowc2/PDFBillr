import warnings

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import csrf, db, limiter, login_manager, mail


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Warn if using insecure default secret
    if app.config["SECRET_KEY"] == "dev-only-insecure-default-do-not-use-in-production":
        warnings.warn("SECRET_KEY env var not set. Using insecure default.", stacklevel=1)

    if not app.config.get("STRIPE_WEBHOOK_SECRET"):
        warnings.warn("STRIPE_WEBHOOK_SECRET env var not set. Webhook signature verification will fail.", stacklevel=1)

    # Proxy fix for reverse-proxy deployments
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

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

    # Create DB tables on first run
    with app.app_context():
        db.create_all()

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
        )
        return response

    # Template context processor: inject is_pro() for all templates
    from utils.gating import is_pro
    @app.context_processor
    def inject_pro():
        return {"is_pro": is_pro}

    return app


# Expose module-level app for gunicorn (`app:app`)
app = create_app()

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8000)
