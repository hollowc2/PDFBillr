from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from extensions import db, limiter, mail, login_manager
from models import User

bp = Blueprint("auth", __name__, url_prefix="/auth")

_TOKEN_SALT = "password-reset"
_TOKEN_MAX_AGE = 3600  # 1 hour


def _get_serializer():
    from flask import current_app
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


# ---------------------------------------------------------------------------
# Flask-Login user loader
# ---------------------------------------------------------------------------

@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("auth/register.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/register.html")

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/register.html")

        if User.query.filter_by(email=email).first():
            current_app.logger.info(
                "Blocked duplicate registration: email=%s ip=%s", email, request.remote_addr
            )
            flash("Registration failed. Please check your details.", "error")
            return render_template("auth/register.html")

        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

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
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            current_app.logger.warning(
                "Failed login: email=%s ip=%s", email, request.remote_addr
            )
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("This account has been disabled.", "error")
            return render_template("auth/login.html")

        login_user(user, remember=remember)
        next_page = request.args.get("next") or url_for("dashboard.index")
        # Prevent open redirect
        from urllib.parse import urlparse
        if urlparse(next_page).netloc:
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
        email = request.form.get("email", "").strip().lower()
        user  = User.query.filter_by(email=email).first()
        # Always show success to prevent email enumeration
        if user:
            _send_reset_email(user)
        flash("If that email is registered, a reset link has been sent.", "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


def _send_reset_email(user: User) -> None:
    from flask_mail import Message
    token = _get_serializer().dumps(user.email, salt=_TOKEN_SALT)
    reset_url = url_for("auth.reset_password", token=token, _external=True)
    msg = Message(
        subject="Reset your PDFBillr password",
        recipients=[user.email],
        body=render_template("emails/reset_password.txt", reset_url=reset_url),
    )
    try:
        mail.send(msg)
    except Exception:
        pass  # Fail silently to prevent email enumeration


def _send_welcome_email(user: User) -> None:
    from flask_mail import Message
    msg = Message(
        subject="Welcome to PDFBillr",
        recipients=[user.email],
        body=render_template("emails/welcome.txt", user=user),
    )
    try:
        mail.send(msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------

@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    try:
        email = _get_serializer().loads(token, salt=_TOKEN_SALT, max_age=_TOKEN_MAX_AGE)
    except (SignatureExpired, BadSignature):
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("User not found.", "error")
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
