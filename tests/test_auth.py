from __future__ import annotations

from extensions import db, mail
from models import User


def test_registration_hashes_password_and_authenticates(client, app):
    response = client.post(
        "/auth/register",
        data={
            "email": "person@example.test",
            "password": "long password",
            "confirm_password": "long password",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="person@example.test").one()
        assert user.password_hash != "long password"
        assert user.check_password("long password")

    dashboard = client.get("/dashboard/")
    assert dashboard.status_code == 200


def test_registration_rejects_malformed_email(client, app):
    response = client.post(
        "/auth/register",
        data={
            "email": "not-an-email",
            "password": "long password",
            "confirm_password": "long password",
        },
    )
    assert response.status_code == 200
    assert b"valid email" in response.data
    with app.app_context():
        assert User.query.count() == 0


def test_login_logout_and_unauthenticated_redirect(client, make_user):
    make_user("person@example.test")

    denied = client.get("/dashboard/")
    assert denied.status_code == 302
    assert "/auth/login" in denied.headers["Location"]

    login_response = client.post(
        "/auth/login",
        data={"email": "person@example.test", "password": "correct horse"},
    )
    assert login_response.status_code == 302
    assert client.get("/dashboard/").status_code == 200

    logout_response = client.get("/auth/logout")
    assert logout_response.status_code == 302
    assert client.get("/dashboard/").status_code == 302


def test_inactive_user_existing_session_is_rejected(client, app, make_user, login):
    user = make_user("person@example.test")
    login("person@example.test")
    assert client.get("/dashboard/").status_code == 200

    with app.app_context():
        stored = db.session.get(User, user.id)
        stored.is_active = False
        db.session.commit()

    assert client.get("/dashboard/").status_code == 302


def test_remember_cookie_is_hardened(client, make_user, login):
    make_user("person@example.test")
    response = login("person@example.test", remember=True)
    cookies = response.headers.getlist("Set-Cookie")
    remember = next(value for value in cookies if value.startswith("remember_token="))

    assert "Secure" in remember
    assert "HttpOnly" in remember
    assert "SameSite=Lax" in remember


def test_login_next_accepts_only_local_absolute_path(client, make_user):
    make_user("person@example.test")
    credentials = {"email": "person@example.test", "password": "correct horse"}

    for target in (
        "https://attacker.example",
        "https:attacker.example",
        "//attacker.example/path",
        r"\attacker.example",
    ):
        response = client.post(f"/auth/login?next={target}", data=credentials)
        assert response.headers["Location"].endswith("/dashboard/")
        client.get("/auth/logout")

    response = client.post("/auth/login?next=/app", data=credentials)
    assert response.headers["Location"].endswith("/app")


def test_reset_email_uses_canonical_origin_not_forwarded_host(
    client, app, make_user
):
    make_user("person@example.test")

    with app.app_context(), mail.record_messages() as outbox:
        response = client.post(
            "/auth/forgot-password",
            data={"email": "person@example.test"},
            headers={
                "Host": "localhost",
                "X-Forwarded-Host": "attacker.example",
                "X-Forwarded-Proto": "http",
            },
        )

    assert response.status_code == 302
    assert len(outbox) == 1
    assert "https://billing.example.test/pdfbillr/auth/reset-password/" in outbox[0].body
    assert "attacker.example" not in outbox[0].body


def test_password_reset_token_is_single_use(client, app, make_user):
    make_user("person@example.test")
    with app.app_context(), mail.record_messages() as outbox:
        client.post(
            "/auth/forgot-password",
            data={"email": "person@example.test"},
        )
    reset_url = next(
        line for line in outbox[0].body.splitlines()
        if "/auth/reset-password/" in line
    )
    token_path = f"/auth/reset-password/{reset_url.rsplit('/', 1)[1]}"

    first = client.post(
        token_path,
        data={"password": "new secure password", "confirm_password": "new secure password"},
    )
    second = client.post(
        token_path,
        data={"password": "attacker password", "confirm_password": "attacker password"},
    )

    assert first.status_code == 302
    assert second.status_code == 302
    assert second.headers["Location"].endswith("/auth/forgot-password")
    with app.app_context():
        user = User.query.filter_by(email="person@example.test").one()
        assert user.check_password("new secure password")
        assert not user.check_password("attacker password")


def test_password_reset_revokes_existing_sessions_and_remember_cookie(
    app, make_user
):
    make_user("person@example.test")
    session_client = app.test_client()
    remember_client = app.test_client()
    reset_client = app.test_client()

    assert session_client.post(
        "/auth/login",
        data={"email": "person@example.test", "password": "correct horse"},
    ).status_code == 302
    assert remember_client.post(
        "/auth/login",
        data={
            "email": "person@example.test",
            "password": "correct horse",
            "remember": "1",
        },
    ).status_code == 302
    remember_client.delete_cookie("session")

    with app.app_context(), mail.record_messages() as outbox:
        reset_client.post(
            "/auth/forgot-password",
            data={"email": "person@example.test"},
        )
    reset_url = next(
        line
        for line in outbox[0].body.splitlines()
        if "/auth/reset-password/" in line
    )
    token_path = f"/auth/reset-password/{reset_url.rsplit('/', 1)[1]}"
    assert reset_client.post(
        token_path,
        data={
            "password": "new secure password",
            "confirm_password": "new secure password",
        },
    ).status_code == 302

    assert session_client.get("/dashboard/").status_code == 302
    remember_response = remember_client.get("/dashboard/")
    assert remember_response.status_code == 302
    remember_headers = remember_response.headers.getlist("Set-Cookie")
    assert any(
        value.startswith("remember_token=;") and "Expires=" in value
        for value in remember_headers
    )


def test_login_identity_includes_current_auth_session_version(
    client, app, make_user, login
):
    user = make_user("person@example.test")
    login(user.email)

    with client.session_transaction() as browser_session:
        assert browser_session["_user_id"] == f"{user.id}:1"

    with app.app_context():
        stored = db.session.get(User, user.id)
        stored.set_password("a replacement password")
        db.session.commit()
        assert stored.auth_session_version == 2

    assert client.get("/dashboard/").status_code == 302
