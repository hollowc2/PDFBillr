from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from itertools import zip_longest

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict

from extensions import db, limiter
from models import BusinessDefaults, Client, Estimate, Invoice, ServiceItem
from utils.currency import SUPPORTED_CURRENCIES
from utils.estimate_numbers import (
    estimate_number_exists,
    next_available_estimate_number,
)
from utils.financial_shadow_values import invoice_shadow_values
from utils.gating import is_pro
from utils.invoice_calculations import InvoiceCalculationError, calculate_invoice
from utils.invoice_numbers import next_available_invoice_number
from utils.urls import external_url
from utils.validation import is_valid_email, normalize_email


bp = Blueprint("estimates", __name__, url_prefix="/estimates")

_TERMINAL_STATUSES = {"accepted", "declined", "expired", "converted"}
_MAX_LONG_TEXT = 5000
_MAX_COMMENT = 2000


@bp.route("/")
@login_required
def index():
    estimates = (
        Estimate.query.filter_by(user_id=current_user.id)
        .order_by(Estimate.created_at.desc(), Estimate.id.desc())
        .all()
    )
    return render_template("estimates/index.html", estimates=estimates)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "GET":
        return _render_form(None, _initial_form_data())

    values, error = _estimate_values(request.form)
    if error:
        flash(error, "error")
        return _render_form(None, request.form), 400
    if estimate_number_exists(current_user.id, values["estimate_number"]):
        flash("That estimate number is already in use.", "error")
        return _render_form(None, request.form), 400

    estimate = Estimate(
        user_id=current_user.id,
        public_token=secrets.token_urlsafe(32),
        status="draft",
        **values,
    )
    db.session.add(estimate)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if estimate_number_exists(current_user.id, values["estimate_number"]):
            flash(
                "That estimate number was just used by another request.",
                "error",
            )
            return _render_form(None, request.form), 409
        raise

    flash("Estimate created.", "success")
    return redirect(url_for("estimates.detail", estimate_id=estimate.id))


@bp.route("/<int:estimate_id>")
@login_required
def detail(estimate_id: int):
    estimate = _own_estimate(estimate_id)
    _persist_expiry(estimate)
    public_url = (
        external_url("estimates.public_view", token=estimate.public_token)
        if estimate.status != "draft"
        else None
    )
    return render_template(
        "estimates/detail.html",
        estimate=estimate,
        line_items=_line_items(estimate),
        public_url=public_url,
    )


@bp.route("/<int:estimate_id>/edit", methods=["GET", "POST"])
@login_required
def edit(estimate_id: int):
    estimate = _own_estimate(estimate_id, for_update=request.method == "POST")
    _persist_expiry(estimate)
    if estimate.status in _TERMINAL_STATUSES:
        flash("Accepted, declined, expired, and converted estimates are read-only.", "error")
        return redirect(url_for("estimates.detail", estimate_id=estimate.id))

    if request.method == "GET":
        return _render_form(estimate, _estimate_form_data(estimate))

    values, error = _estimate_values(request.form)
    if error:
        flash(error, "error")
        return _render_form(estimate, request.form), 400
    if estimate_number_exists(
        current_user.id,
        values["estimate_number"],
        exclude_id=estimate.id,
    ):
        flash("That estimate number is already in use.", "error")
        return _render_form(estimate, request.form), 400

    for field, value in values.items():
        setattr(estimate, field, value)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if estimate_number_exists(
            current_user.id,
            values["estimate_number"],
            exclude_id=estimate.id,
        ):
            flash(
                "That estimate number was just used by another request.",
                "error",
            )
            return _render_form(_own_estimate(estimate_id), request.form), 409
        raise

    flash("Estimate updated.", "success")
    return redirect(url_for("estimates.detail", estimate_id=estimate.id))


@bp.route("/<int:estimate_id>/send", methods=["POST"])
@login_required
def send(estimate_id: int):
    estimate = _own_estimate(estimate_id, for_update=True)
    _persist_expiry(estimate)
    if estimate.status in _TERMINAL_STATUSES:
        flash("This estimate can no longer be sent.", "error")
    else:
        estimate.status = "sent"
        estimate.sent_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Estimate marked as sent. Its public response link is now active.", "success")
    return redirect(url_for("estimates.detail", estimate_id=estimate.id))


@bp.route("/<int:estimate_id>/convert", methods=["POST"])
@login_required
def convert(estimate_id: int):
    estimate = _own_estimate(estimate_id, for_update=True)
    _persist_expiry(estimate)
    if estimate.status == "converted" and estimate.converted_invoice_id:
        return redirect(
            url_for(
                "dashboard.invoice_detail",
                invoice_id=estimate.converted_invoice_id,
            )
        )
    if estimate.status != "accepted":
        flash("Only an accepted estimate can be converted to an invoice.", "error")
        return redirect(url_for("estimates.detail", estimate_id=estimate.id))

    try:
        calculated = calculate_invoice(
            _line_items(estimate),
            tax_rate=estimate.tax_rate,
            discount=estimate.discount,
            currency_code=estimate.currency_code,
        )
    except InvoiceCalculationError:
        # Persisted estimates are canonical. Reaching this branch means the
        # record was externally corrupted, so never create a questionable bill.
        abort(409)

    invoice_date = date.today()
    terms_days = _payment_terms_days(estimate)
    due_date = invoice_date + timedelta(days=terms_days)
    preferred_number = _preferred_invoice_number(estimate.estimate_number)
    invoice_number = next_available_invoice_number(
        current_user.id,
        preferred_number,
    )
    shadows = invoice_shadow_values(
        invoice_date=invoice_date.isoformat(),
        due_date=due_date.isoformat(),
        tax_rate=calculated.tax_rate,
        discount=calculated.discount,
        subtotal=calculated.subtotal,
        total=calculated.total,
    )
    invoice = Invoice(
        user_id=current_user.id,
        client_id=estimate.client_id,
        invoice_number=invoice_number,
        currency_code=estimate.currency_code,
        invoice_date=invoice_date.isoformat(),
        due_date=due_date.isoformat(),
        invoice_date_value=shadows["invoice_date_value"],
        due_date_value=shadows["due_date_value"],
        from_company=estimate.from_company,
        from_address=estimate.from_address,
        from_email=estimate.from_email,
        from_phone=estimate.from_phone,
        to_name=estimate.to_name,
        to_address=estimate.to_address,
        to_email=estimate.to_email,
        line_items_json=json.dumps(calculated.line_items),
        tax_rate=float(calculated.tax_rate),
        discount=float(calculated.discount),
        subtotal=float(calculated.subtotal),
        total=float(calculated.total),
        tax_rate_decimal=shadows["tax_rate_decimal"],
        discount_decimal=shadows["discount_decimal"],
        subtotal_decimal=shadows["subtotal_decimal"],
        total_decimal=shadows["total_decimal"],
        notes=estimate.notes,
        payment_info=estimate.payment_info,
        payment_url=(
            current_user.business_defaults.default_payment_url
            if current_user.business_defaults is not None
            else None
        ),
        logo_filename=(
            current_user.branding.logo_filename
            if is_pro() and current_user.branding is not None
            else None
        ),
        theme="default",
        status="draft",
        view_token=secrets.token_urlsafe(32),
    )
    db.session.add(invoice)
    db.session.flush()
    estimate.status = "converted"
    estimate.converted_invoice_id = invoice.id
    estimate.converted_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        refreshed = _own_estimate(estimate_id)
        if refreshed.converted_invoice_id:
            return redirect(
                url_for(
                    "dashboard.invoice_detail",
                    invoice_id=refreshed.converted_invoice_id,
                )
            )
        raise

    flash(f"Draft invoice {invoice.invoice_number} created.", "success")
    return redirect(
        url_for("dashboard.invoice_detail", invoice_id=invoice.id)
    )


@bp.route("/view/<token>")
@limiter.limit("60 per minute")
def public_view(token: str):
    estimate = _public_estimate(token)
    _persist_expiry(estimate)
    return render_template(
        "estimates/public.html",
        estimate=estimate,
        line_items=_line_items(estimate),
    )


@bp.route("/view/<token>/accept", methods=["POST"])
@limiter.limit("10 per minute")
def public_accept(token: str):
    return _public_response(token, "accepted")


@bp.route("/view/<token>/decline", methods=["POST"])
@limiter.limit("10 per minute")
def public_decline(token: str):
    return _public_response(token, "declined")


def _public_response(token: str, new_status: str):
    estimate = _public_estimate(token, for_update=True)
    _persist_expiry(estimate)
    if estimate.status != "sent":
        flash("This estimate can no longer be changed.", "error")
        return redirect(url_for("estimates.public_view", token=token))

    comment = (request.form.get("client_comment") or "").strip()
    if len(comment) > _MAX_COMMENT:
        flash(f"Comments are limited to {_MAX_COMMENT} characters.", "error")
        return redirect(url_for("estimates.public_view", token=token))

    estimate.status = new_status
    estimate.client_comment = comment or None
    estimate.responded_at = datetime.now(timezone.utc)
    db.session.commit()
    flash(
        "Estimate accepted." if new_status == "accepted" else "Estimate declined.",
        "success",
    )
    return redirect(url_for("estimates.public_view", token=token))


def _own_estimate(estimate_id: int, *, for_update: bool = False) -> Estimate:
    query = Estimate.query.filter_by(id=estimate_id, user_id=current_user.id)
    if for_update:
        query = query.with_for_update()
    estimate = query.first()
    if estimate is None:
        abort(404)
    return estimate


def _public_estimate(token: str, *, for_update: bool = False) -> Estimate:
    if len(token) > 64:
        abort(404)
    query = Estimate.query.filter_by(public_token=token)
    if for_update:
        query = query.with_for_update()
    estimate = query.first()
    if estimate is None or estimate.status == "draft":
        abort(404)
    return estimate


def _persist_expiry(estimate: Estimate) -> None:
    if estimate.effective_status() == "expired" and estimate.status != "expired":
        estimate.status = "expired"
        db.session.commit()


def _estimate_values(form) -> tuple[dict | None, str | None]:
    estimate_number = (form.get("estimate_number") or "").strip()
    if not estimate_number:
        return None, "Estimate number is required."
    if len(estimate_number) > 200:
        return None, "Estimate number must be 200 characters or fewer."

    issue_date, error = _date_value(form.get("issue_date"), "Issue date")
    if error:
        return None, error
    expiry_date, error = _date_value(form.get("expiry_date"), "Expiry date")
    if error:
        return None, error
    if expiry_date < issue_date:
        return None, "Expiry date cannot be before the issue date."

    currency_code = (form.get("currency_code") or "USD").strip().upper()
    if currency_code not in SUPPORTED_CURRENCIES:
        return None, "Choose a supported currency."

    client = _selected_client(form.get("client_id"))
    short_values = {}
    for field, label in (
        ("from_company", "Company name"),
        ("from_email", "Business email"),
        ("from_phone", "Phone"),
        ("to_name", "Client name"),
        ("to_email", "Client email"),
    ):
        value = (form.get(field) or "").strip()
        if len(value) > 200:
            return None, f"{label} must be 200 characters or fewer."
        short_values[field] = value or None

    if client is not None:
        short_values["to_name"] = short_values["to_name"] or client.name
        short_values["to_email"] = short_values["to_email"] or client.email
    if not short_values["to_name"]:
        return None, "Client name is required."
    for field, label in (("from_email", "business"), ("to_email", "client")):
        value = normalize_email(short_values[field])
        if value and not is_valid_email(value):
            return None, f"Enter a valid {label} email address."
        short_values[field] = value or None

    long_values = {}
    for field, label in (
        ("from_address", "Business address"),
        ("to_address", "Client address"),
        ("notes", "Notes"),
        ("payment_info", "Payment information"),
    ):
        value = (form.get(field) or "").strip()
        if len(value) > _MAX_LONG_TEXT:
            return None, f"{label} must be {_MAX_LONG_TEXT} characters or fewer."
        long_values[field] = value or None
    if client is not None:
        long_values["to_address"] = long_values["to_address"] or client.address

    raw_items = [
        {"description": description, "qty": quantity, "rate": rate}
        for description, quantity, rate in zip_longest(
            form.getlist("description[]"),
            form.getlist("qty[]"),
            form.getlist("rate[]"),
            fillvalue="",
        )
    ]
    try:
        calculated = calculate_invoice(
            raw_items,
            tax_rate=form.get("tax_rate"),
            discount=form.get("discount"),
            currency_code=currency_code,
        )
    except InvoiceCalculationError as exc:
        return None, str(exc)
    if not calculated.line_items:
        return None, "Add at least one line item with a description."

    return {
        "client_id": client.id if client is not None else None,
        "estimate_number": estimate_number,
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "currency_code": currency_code,
        **short_values,
        **long_values,
        "line_items_json": json.dumps(calculated.line_items),
        "tax_rate": calculated.tax_rate,
        "discount": calculated.discount,
        "subtotal": calculated.subtotal,
        "total": calculated.total,
    }, None


def _date_value(raw_value, label: str) -> tuple[date | None, str | None]:
    try:
        return date.fromisoformat((raw_value or "").strip()), None
    except (TypeError, ValueError):
        return None, f"{label} must be a valid date."


def _selected_client(raw_client_id) -> Client | None:
    if raw_client_id in (None, ""):
        return None
    try:
        client_id = int(raw_client_id)
    except (TypeError, ValueError):
        abort(404)
    client = Client.query.filter_by(
        id=client_id,
        user_id=current_user.id,
    ).first()
    if client is None:
        abort(404)
    return client


def _line_items(estimate: Estimate) -> list[dict]:
    try:
        items = json.loads(estimate.line_items_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return items if isinstance(items, list) else []


def _initial_form_data() -> MultiDict:
    today = date.today()
    defaults = current_user.business_defaults
    terms_days = defaults.default_payment_terms_days if defaults else 30
    initial = MultiDict(
        {
            "estimate_number": next_available_estimate_number(current_user.id),
            "issue_date": today.isoformat(),
            "expiry_date": (today + timedelta(days=terms_days)).isoformat(),
            "currency_code": "USD",
            "tax_rate": str(defaults.default_tax_rate if defaults else 0),
            "discount": "0",
            "from_company": defaults.from_company if defaults else "",
            "from_address": defaults.from_address if defaults else "",
            "from_email": defaults.from_email if defaults else "",
            "from_phone": defaults.from_phone if defaults else "",
            "notes": defaults.default_notes if defaults else "",
            "payment_info": defaults.default_payment_info if defaults else "",
        }
    )
    client = _selected_client(request.args.get("client_id"))
    if client is not None:
        initial["client_id"] = str(client.id)
        initial["to_name"] = client.name
        initial["to_address"] = client.address or ""
        initial["to_email"] = client.email or ""
        if client.default_tax_rate is not None:
            initial["tax_rate"] = str(client.default_tax_rate)
        if client.default_payment_terms_days is not None:
            initial["expiry_date"] = (
                today + timedelta(days=client.default_payment_terms_days)
            ).isoformat()

    service_id = request.args.get("service_id")
    if service_id not in (None, ""):
        try:
            parsed_service_id = int(service_id)
        except ValueError:
            abort(404)
        service = ServiceItem.query.filter_by(
            id=parsed_service_id,
            user_id=current_user.id,
        ).first()
        if service is None:
            abort(404)
        initial.add("description[]", service.description)
        initial.add("qty[]", str(service.default_quantity))
        initial.add("rate[]", str(service.default_rate))
    return initial


def _estimate_form_data(estimate: Estimate) -> MultiDict:
    data = MultiDict(
        {
            "estimate_number": estimate.estimate_number,
            "issue_date": estimate.issue_date.isoformat(),
            "expiry_date": estimate.expiry_date.isoformat(),
            "currency_code": estimate.currency_code,
            "client_id": str(estimate.client_id or ""),
            "from_company": estimate.from_company or "",
            "from_address": estimate.from_address or "",
            "from_email": estimate.from_email or "",
            "from_phone": estimate.from_phone or "",
            "to_name": estimate.to_name or "",
            "to_address": estimate.to_address or "",
            "to_email": estimate.to_email or "",
            "tax_rate": str(estimate.tax_rate),
            "discount": str(estimate.discount),
            "notes": estimate.notes or "",
            "payment_info": estimate.payment_info or "",
        }
    )
    for item in _line_items(estimate):
        data.add("description[]", item.get("description", ""))
        data.add("qty[]", item.get("qty", ""))
        data.add("rate[]", item.get("rate", ""))
    return data


def _render_form(estimate: Estimate | None, form_data):
    descriptions = form_data.getlist("description[]")
    quantities = form_data.getlist("qty[]")
    rates = form_data.getlist("rate[]")
    form_items = [
        {"description": description, "qty": quantity, "rate": rate}
        for description, quantity, rate in zip_longest(
            descriptions,
            quantities,
            rates,
            fillvalue="",
        )
    ] or [{"description": "", "qty": "1", "rate": "0"}]
    return render_template(
        "estimates/form.html",
        estimate=estimate,
        form_data=form_data,
        form_items=form_items,
        clients=(
            Client.query.filter_by(user_id=current_user.id)
            .order_by(Client.name.asc())
            .all()
        ),
        services=(
            ServiceItem.query.filter_by(user_id=current_user.id)
            .order_by(ServiceItem.name.asc())
            .all()
        ),
    )


def _payment_terms_days(estimate: Estimate) -> int:
    client = estimate.client
    if client is not None and client.default_payment_terms_days is not None:
        return client.default_payment_terms_days
    defaults = BusinessDefaults.query.filter_by(user_id=estimate.user_id).first()
    return defaults.default_payment_terms_days if defaults is not None else 30


def _preferred_invoice_number(estimate_number: str) -> str:
    if re.match(r"(?i)^EST", estimate_number):
        return re.sub(r"(?i)^EST", "INV", estimate_number, count=1)
    return f"INV-{estimate_number}"[:200]
