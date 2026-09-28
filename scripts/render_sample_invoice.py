"""Render the sample invoice shown on the landing page and in the README.

Runs the real PDF pipeline (build_invoice_context + render_pdf) with fixed
sample data, then rasterizes page 1 with poppler's ``pdftoppm``.

    make sample-invoice
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from werkzeug.datastructures import MultiDict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE = MultiDict(
    [
        ("invoice_number", "INV-2026-042"),
        ("invoice_date", "2026-09-22"),
        ("due_date", "2026-10-22"),
        ("currency_code", "USD"),
        ("from_company", "Mara Lin Design"),
        ("from_address", "18 Harbor Street\nPortland, ME 04101"),
        ("from_email", "hello@maralin.design"),
        ("from_phone", "+1 207 555 0142"),
        ("to_name", "Northwind Studio"),
        ("to_address", "400 Commerce Ave, Suite 12\nBoston, MA 02110"),
        ("to_email", "ap@northwind.example"),
        ("description[]", "Homepage redesign"),
        ("qty[]", "5"),
        ("rate[]", "150"),
        ("description[]", "Frontend build"),
        ("qty[]", "8"),
        ("rate[]", "200"),
        ("description[]", "Strategy session"),
        ("qty[]", "2"),
        ("rate[]", "250"),
        ("tax_rate", "10"),
        ("discount", "50"),
        ("notes", "Thank you for your business. Payment is due within 30 days."),
        ("payment_info", "Bank transfer: Example Bank\nAccount: 0000 4821\nRouting: 000000000"),
    ]
)

OUTPUTS = [ROOT / "static" / "img" / "sample-invoice.png"]
DPI = 144
# Crop to the part of the A4 page that has content (pixels at DPI).
CROP_HEIGHT = 1250


def main() -> int:
    if not shutil.which("pdftoppm"):
        print("pdftoppm not found; install poppler-utils.", file=sys.stderr)
        return 1

    os.environ.setdefault("SECRET_KEY", "sample-invoice-render-only-0000000000000000")
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    os.environ.setdefault("DISABLE_SCHEDULER", "true")

    from app import create_app
    from utils.pdf import build_invoice_context, render_pdf

    app = create_app()
    with app.test_request_context():
        pdf = render_pdf(build_invoice_context(SAMPLE), theme="default")

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "sample.pdf"
        pdf_path.write_bytes(pdf)
        prefix = Path(tmp) / "page"
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(DPI), "-f", "1", "-l", "1",
             "-H", str(CROP_HEIGHT), "-singlefile", str(pdf_path), str(prefix)],
            check=True,
        )
        for output in OUTPUTS:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(prefix.with_suffix(".png"), output)
            print(f"wrote {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
