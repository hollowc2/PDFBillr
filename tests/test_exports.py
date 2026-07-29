from __future__ import annotations

import csv
import io
from datetime import date

import pytest


def _csv_rows(response):
    decoded = response.data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(decoded)))


def test_invoice_export_requires_login(client):
    response = client.get("/dashboard/invoices.csv")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_invoice_export_is_owner_scoped_and_spreadsheet_safe(
    client, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    other = make_user("other@example.test")
    make_invoice(
        owner.id,
        invoice_number="+SUM(1,1)",
        invoice_date="2026-07-28",
        due_date="2026-08-28",
        to_name="  =HYPERLINK(\"https://example.test\")",
        to_email="\t=cmd",
        from_company="@malicious",
        total=42.5,
    )
    make_invoice(
        other.id,
        invoice_number="PRIVATE-001",
        to_name="Other user's client",
    )
    login(owner.email)

    response = client.get("/dashboard/invoices.csv")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.headers["Content-Disposition"].startswith(
        'attachment; filename="pdfbillr-invoices-'
    )
    assert response.data.startswith(b"\xef\xbb\xbf")

    rows = _csv_rows(response)
    assert len(rows) == 1
    assert rows[0]["Invoice Number"] == "'+SUM(1,1)"
    assert rows[0]["Client Name"].startswith("'  =HYPERLINK")
    assert rows[0]["Client Email"] == "'\t=cmd"
    assert rows[0]["From Company"] == "'@malicious"
    assert rows[0]["Amount Paid"] == "0.00"
    assert rows[0]["Balance Due"] == "42.50"
    assert "PRIVATE-001" not in response.get_data(as_text=True)


def test_invoice_export_combines_search_status_and_date_filters(
    client, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    make_invoice(
        owner.id,
        invoice_number="MATCH-001",
        invoice_date="2026-07-10",
        invoice_date_value=date(2026, 7, 10),
        to_name="Alpha Client",
        status="sent",
    )
    make_invoice(
        owner.id,
        invoice_number="WRONG-STATUS",
        invoice_date="2026-07-11",
        to_name="Alpha Client",
        status="draft",
    )
    make_invoice(
        owner.id,
        invoice_number="WRONG-DATE",
        invoice_date="2026-08-01",
        to_name="Alpha Client",
        status="sent",
    )
    make_invoice(
        owner.id,
        invoice_number="WRONG-SEARCH",
        invoice_date="2026-07-12",
        to_name="Beta Client",
        status="sent",
    )
    login(owner.email)

    response = client.get(
        "/dashboard/invoices.csv",
        query_string={
            "q": "Alpha",
            "status": "sent",
            "date_from": "2026-07-01",
            "date_to": "2026-07-31",
        },
    )

    assert response.status_code == 200
    rows = _csv_rows(response)
    assert [row["Invoice Number"] for row in rows] == ["MATCH-001"]


@pytest.mark.parametrize(
    "query_string",
    (
        {"date_from": "07/01/2026"},
        {"date_to": "not-a-date"},
        {"date_from": "2026-08-01", "date_to": "2026-07-01"},
        {"status": "=sent"},
    ),
)
def test_invoice_export_rejects_invalid_filters(
    client, make_user, login, query_string
):
    owner = make_user("owner@example.test")
    login(owner.email)

    response = client.get("/dashboard/invoices.csv", query_string=query_string)

    assert response.status_code == 400


def test_dashboard_exposes_export_to_free_accounts(
    client, make_user, login
):
    owner = make_user("owner@example.test")
    login(owner.email)

    response = client.get("/dashboard/")

    assert response.status_code == 200
    assert b"Export CSV" in response.data
    assert b"/dashboard/invoices.csv" in response.data


def test_product_copy_matches_free_persistence_and_pro_features(client):
    landing = client.get("/")
    upgrade = client.get("/billing/upgrade")

    assert landing.status_code == 200
    assert b"Anonymous invoices are not stored" in landing.data
    assert b"Optional free account for history" in landing.data
    assert b"Client history &amp; saved invoices" not in landing.data
    assert upgrade.status_code == 200
    assert b"Invoice history" not in upgrade.data
    assert b"Automatic payment reminders" in upgrade.data
