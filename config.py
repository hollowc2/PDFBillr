import os


_INSECURE_DEFAULT_SECRET = "dev-only-insecure-default-do-not-use-in-production"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be one of true/false, yes/no, on/off, or 1/0")


def _env_list(name: str) -> list[str] | None:
    values = [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]
    return values or None


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
    SECRET_KEY = os.environ.get("SECRET_KEY") or _INSECURE_DEFAULT_SECRET
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", False)
    TRUSTED_HOSTS = _env_list("TRUSTED_HOSTS")
    AUTO_CREATE_DB = _env_bool("AUTO_CREATE_DB", APP_ENV != "production")

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///pdfbillr.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Flask-Mail
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "localhost")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@pdfbillr.com")

    # Stripe
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_ID_PRO = os.environ.get("STRIPE_PRICE_ID_PRO", "")

    # Rate limiting
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # File uploads (2 MB max)
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER",
        os.path.join(os.path.dirname(__file__), "instance", "uploads"),
    )
    MAX_LOGO_PIXELS = int(os.environ.get("MAX_LOGO_PIXELS", 10_000_000))
    MAX_LOGO_DIMENSION = int(os.environ.get("MAX_LOGO_DIMENSION", 4096))

    # Session cookie security
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", True)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    ENABLE_HSTS = _env_bool("ENABLE_HSTS", False)

    # Scheduler — set DISABLE_SCHEDULER=true to turn off background jobs (dev/testing)
    DISABLE_SCHEDULER = _env_bool("DISABLE_SCHEDULER", False)
    SCHEDULER_TIMEZONE = os.environ.get("SCHEDULER_TIMEZONE", "UTC").strip()
