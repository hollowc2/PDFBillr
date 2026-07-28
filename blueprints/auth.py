import hashlib

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from extensions import db, limiter, mail, login_manager
from models import User
from utils.urls import external_url
from utils.validation import is_valid_email, normalize_email

bp = Blueprint("auth", __name__, url_prefix="/auth")

_TOKEN_SALT = "password-reset"
_TOKEN_MAX_AGE = 3600  # 1 hour


def _get_serializer():
    from flask import current_app
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def _password_token_version(user: User) -> str:
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Flask-Login user loader
# ---------------------------------------------------------------------------

@login_manager.user_loader
def load_user(authenticated_id: str):
    try:
        user_id, separator, raw_version = authenticated_id.partition(":")
        if not separator:
            raise ValueError
        session_version = int(raw_version)
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        _clear_invalid_auth_state()
        return None
    if (
        user is None
        or not user.is_active
        or user.auth_session_version != session_version
    ):
        _clear_invalid_auth_state()
        return None
    return user


def _clear_invalid_auth_state() -> None:
    """Remove a rejected session and expire any associated remember cookie."""
    session.pop("_user_id", None)
    session["_remember"] = "clear"


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email    = normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if not is_valid_email(email) or not password:
            flash("A valid email and password are required.", "error")
            return render_template("auth/register.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/register.html")

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/register.html")

        if User.query.filter_by(email=email).first():
            current_app.logger.info(
                "Blocked duplicate registration: ip=%s", request.remote_addr
            )
            flash("Registration failed. Please check your details.", "error")
            return render_template("auth/register.html")

        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        session.clear()
        login_user(user, remember=True)
        _send_welcome_email(user)
        flash("Account created! Welcome to PDFBillr.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/register.html")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email    = normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first() if is_valid_email(email) else None
        if not user or not user.check_password(password):
            current_app.logger.warning(
                "Failed login: ip=%s", request.remote_addr
            )
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("This account has been disabled.", "error")
            return render_template("auth/login.html")

        session.clear()
        login_user(user, remember=remember)
        next_page = request.args.get("next") or url_for("dashboard.index")
        # Accept only a local absolute path. Schemes, protocol-relative URLs,
        # backslashes, and control characters are rejected.
        if (
            not next_page.startswith("/")
            or next_page.startswith("//")
            or "\\" in next_page
            or any(ord(char) < 32 for char in next_page)
        ):
            next_page = url_for("dashboard.index")
        return redirect(next_page)

    return render_template("auth/login.html")


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("public.landing"))


# ---------------------------------------------------------------------------
# Forgot password
# ---------------------------------------------------------------------------

@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def forgot_password():
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        user = (
            User.query.filter_by(email=email).first()
            if is_valid_email(email)
            else None
        )
        # Always show success to prevent email enumeration
        if user:
            _send_reset_email(user)
        flash("If that email is registered, a reset link has been sent.", "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


def _send_reset_email(user: User) -> None:
    from flask_mail import Message
    token = _get_serializer().dumps(
        {
            "email": user.email,
            "password_version": _password_token_version(user),
        },
        salt=_TOKEN_SALT,
    )
    reset_url = external_url("auth.reset_password", token=token)
    msg = Message(
        subject="Reset your PDFBillr password",
        recipients=[user.email],
        body=render_template("emails/reset_password.txt", reset_url=reset_url),
    )
    try:
        mail.send(msg)
    except Exception as exc:  # SMTP adapters expose varied exception types.
        # Preserve the enumeration-safe response while retaining an operational
        # signal. Do not log the address, token, or exception message.
        current_app.logger.warning(
            "Password reset email failed: user=%s error=%s",
            user.id,
            type(exc).__name__,
        )


def _send_welcome_email(user: User) -> None:
    from flask_mail import Message
    msg = Message(
        subject="Welcome to PDFBillr",
        recipients=[user.email],
        body=render_template(
            "emails/welcome.txt",
            user=user,
            app_url=external_url("public.index"),
        ),
    )
    try:
        mail.send(msg)
    except Exception as exc:  # SMTP adapters expose varied exception types.
        current_app.logger.warning(
            "Welcome email failed: user=%s error=%s",
            user.id,
            type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------

@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    try:
        payload = _get_serializer().loads(
            token,
            salt=_TOKEN_SALT,
            max_age=_TOKEN_MAX_AGE,
        )
    except (SignatureExpired, BadSignature):
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if not isinstance(payload, dict):
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter_by(email=payload.get("email")).first()
    if (
        not user
        or payload.get("password_version") != _password_token_version(user)
    ):
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/reset_password.html", token=token)

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/reset_password.html", token=token)

        user.set_password(password)
        db.session.commit()
        flash("Password updated. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)
