from __future__ import annotations

import io
import os

import pytest
from PIL import Image

from models import BrandingProfile
from utils.pdf import _restricted_url_fetcher, build_invoice_context, render_pdf


def image_upload(filename="logo.png", *, size=(16, 16)):
    data = io.BytesIO()
    Image.new("RGB", size, color="blue").save(data, format="PNG")
    data.seek(0)
    return data, filename


def test_valid_logo_is_reencoded_and_served_only_to_owner(
    client, app, make_user, login
):
    owner = make_user("owner@example.test", pro=True)
    login(owner.email)
    response = client.post(
        "/dashboard/branding",
        data={"accent_color": "#1e3a8a", "logo": image_upload("../../logo.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302

    with app.app_context():
        profile = BrandingProfile.query.filter_by(user_id=owner.id).one()
        assert profile.logo_filename.startswith(f"{owner.id}_")
        assert profile.logo_filename.endswith(".png")
        assert os.path.isfile(
            os.path.join(app.config["UPLOAD_FOLDER"], profile.logo_filename)
        )

    logo_response = client.get("/dashboard/branding/logo")
    assert logo_response.status_code == 200
    assert logo_response.mimetype == "image/png"

    client.get("/auth/logout")
    other = make_user("other@example.test", pro=True)
    login(other.email)
    assert client.get("/dashboard/branding/logo").status_code == 404


def test_fake_or_malformed_logo_is_rejected_without_storage(
    client, app, make_user, login
):
    user = make_user("person@example.test", pro=True)
    login(user.email)
    response = client.post(
        "/dashboard/branding",
        data={
            "accent_color": "#1e3a8a",
            "logo": (io.BytesIO(b"<html>not an image</html>"), "fake.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"not a valid image" in response.data
    assert not os.path.exists(app.config["UPLOAD_FOLDER"]) or not os.listdir(
        app.config["UPLOAD_FOLDER"]
    )


def test_oversized_dimensions_are_rejected(
    client, app, make_user, login
):
    user = make_user("person@example.test", pro=True)
    login(user.email)
    app.config["MAX_LOGO_DIMENSION"] = 8
    response = client.post(
        "/dashboard/branding",
        data={"accent_color": "#1e3a8a", "logo": image_upload(size=(16, 16))},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"dimensions are too large" in response.data


def test_oversized_upload_is_rejected_by_request_limit(
    client, make_user, login
):
    user = make_user("person@example.test", pro=True)
    login(user.email)
    response = client.post(
        "/dashboard/branding",
        data={
            "accent_color": "#1e3a8a",
            "logo": (io.BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "large.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 413


def test_failed_replacement_preserves_existing_logo(
    client, app, make_user, login, monkeypatch
):
    from utils.uploads import LogoValidationError

    user = make_user("person@example.test", pro=True)
    login(user.email)
    assert client.post(
        "/dashboard/branding",
        data={"accent_color": "#1e3a8a", "logo": image_upload()},
        content_type="multipart/form-data",
    ).status_code == 302
    with app.app_context():
        original = BrandingProfile.query.filter_by(user_id=user.id).one().logo_filename
        original_path = os.path.join(app.config["UPLOAD_FOLDER"], original)
        assert os.path.isfile(original_path)

    monkeypatch.setattr(
        "blueprints.dashboard.store_logo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LogoValidationError("replacement rejected")
        ),
    )
    response = client.post(
        "/dashboard/branding",
        data={"accent_color": "#1e3a8a", "logo": image_upload("replacement.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    with app.app_context():
        profile = BrandingProfile.query.filter_by(user_id=user.id).one()
        assert profile.logo_filename == original
        assert os.path.isfile(original_path)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://example.test/logo.png",
        "file:///etc/passwd",
        "data:text/html;base64,SGVsbG8=",
    ],
)
def test_pdf_fetcher_rejects_external_local_and_non_image_resources(url):
    with pytest.raises(ValueError, match="disabled"):
        _restricted_url_fetcher(url)


def test_hostile_invoice_text_is_escaped_and_pdf_renders(app):
    from werkzeug.datastructures import MultiDict

    form = MultiDict(
        [
            ("invoice_number", "HOSTILE"),
            ("description[]", '<img src="http://169.254.169.254/">'),
            ("qty[]", "1"),
            ("rate[]", "1"),
            ("notes", "<script>alert(1)</script>"),
        ]
    )
    with app.app_context(), app.test_request_context():
        context = build_invoice_context(form)
        pdf = render_pdf(context)
    assert pdf.startswith(b"%PDF")
