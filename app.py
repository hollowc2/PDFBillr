from flask import Flask, render_template, request, make_response, jsonify
from weasyprint import HTML
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import date

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = "change-me-in-production"


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/app")
def index():
    return render_template("form.html", today=date.today().isoformat())


@app.route("/generate", methods=["POST"])
def generate():
    # --- Parse form fields ---
    from_company = request.form.get("from_company", "")
    from_address = request.form.get("from_address", "")
    from_email = request.form.get("from_email", "")
    from_phone = request.form.get("from_phone", "")

    to_name = request.form.get("to_name", "")
    to_address = request.form.get("to_address", "")
    to_email = request.form.get("to_email", "")

    invoice_number = request.form.get("invoice_number", "INV-001")
    invoice_date = request.form.get("invoice_date", date.today().isoformat())
    due_date = request.form.get("due_date", "")

    notes = request.form.get("notes", "")
    payment_info = request.form.get("payment_info", "")

    tax_rate = float(request.form.get("tax_rate", 0) or 0)
    discount = float(request.form.get("discount", 0) or 0)

    action = request.form.get("action", "download")

    # --- Build line items ---
    descriptions = request.form.getlist("description[]")
    qtys = request.form.getlist("qty[]")
    rates = request.form.getlist("rate[]")

    line_items = []
    subtotal = 0.0
    for desc, qty_str, rate_str in zip(descriptions, qtys, rates):
        if not desc.strip():
            continue
        qty = float(qty_str or 0)
        rate = float(rate_str or 0)
        amount = qty * rate
        subtotal += amount
        line_items.append({
            "description": desc,
            "qty": qty,
            "rate": rate,
            "amount": amount,
        })

    tax_amount = subtotal * (tax_rate / 100)
    total = subtotal + tax_amount - discount

    context = {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "from_company": from_company,
        "from_address": from_address,
        "from_email": from_email,
        "from_phone": from_phone,
        "to_name": to_name,
        "to_address": to_address,
        "to_email": to_email,
        "line_items": line_items,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "discount": discount,
        "subtotal": subtotal,
        "total": total,
        "notes": notes,
        "payment_info": payment_info,
    }

    html_string = render_template("invoice.html", **context)
    pdf_bytes = HTML(string=html_string, base_url=request.url_root).write_pdf()

    # Sanitize filename
    safe_number = invoice_number.replace("/", "-").replace("\\", "-")
    filename = f"Invoice-{safe_number}.pdf"

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"

    if action == "preview":
        response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    else:
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    # TODO: Premium — save invoice to DB, send email, associate with current_user

    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=8000)
