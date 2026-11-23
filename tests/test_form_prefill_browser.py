from pathlib import Path
import shutil
import subprocess

import pytest
from werkzeug.datastructures import MultiDict

from blueprints.public import _render_invoice_form


@pytest.mark.parametrize("prefilled", [True, False], ids=["three-items", "no-prefill"])
def test_invoice_form_line_item_rows(app, tmp_path, prefilled):
    chrome = next(
        (
            binary
            for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
            if (binary := shutil.which(name))
        ),
        None,
    )
    if chrome is None:
        pytest.skip("Chrome not installed")

    form_data = None
    if prefilled:
        form_data = MultiDict(
            {
                "invoice_number": "INV-003",
                "from_company": "Example business",
                "to_name": "Example client",
                "invoice_date": "2026-11-22",
                "due_date": "2026-12-22",
                "currency_code": "USD",
            }
        )
        form_data.setlist("description[]", ["Design", "Development", "Review"])
        form_data.setlist("qty[]", ["1", "2", "3"])
        form_data.setlist("rate[]", ["100", "200", "300"])
    with app.test_request_context("/app"):
        html = _render_invoice_form(form_data)
    static_uri = (Path(__file__).resolve().parents[1] / "static").as_uri()
    page = tmp_path / "invoice.html"
    page.write_text(html.replace('"/static/', f'"{static_uri}/'), encoding="utf-8")

    result = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--virtual-time-budget=4000",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert result.stdout.count('name="description[]"') == (3 if prefilled else 1)
