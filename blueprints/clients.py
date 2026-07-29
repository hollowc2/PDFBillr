from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict

from extensions import db
from models import BusinessDefaults, Client, Invoice, ServiceItem
from utils.validation import (
    PaymentURLValidationError,
    is_valid_email,
    normalize_email,
    normalize_payment_url,
)


bp = Blueprint("clients", __name__, url_prefix="/clients")

_MAX_TEXT = 5000
_MAX_RATE = Decimal("9999999999999999.99")
_MAX_QUANTITY = Decimal("99999999999999.9999")


@bp.route("/")
@login_required
def index():
    clients = (
        Client.query.filter_by(user_id=current_user.id)
        .order_by(Client.name.asc())
        .all()
    )
    services = (
        ServiceItem.query.filter_by(user_id=current_user.id)
        .order_by(ServiceItem.name.asc())
        .all()
    )
    return render_template(
        "clients/index.html",
        clients=clients,
        services=services,
        defaults=current_user.business_defaults,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        client, error = _save_client(None)
        if client is not None:
            flash("Client saved.", "success")
            return redirect(url_for("clients.index"))
        flash(error, "error")
    return render_template("clients/client_form.html", client=None)


@bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit(client_id: int):
    client = _own_client(client_id)
    if request.method == "POST":
        saved, error = _save_client(client)
        if saved is not None:
            flash("Client updated.", "success")
            return redirect(url_for("clients.index"))
        flash(error, "error")
    return render_template("clients/client_form.html", client=client)


@bp.route("/<int:client_id>/delete", methods=["POST"])
@login_required
def delete(client_id: int):
    client = _own_client(client_id)
    # Keep immutable invoice snapshots and only remove the optional directory
    # association. Explicitly nulling also behaves consistently in SQLite
    # installations where foreign-key enforcement was historically disabled.
    Invoice.query.filter_by(
        user_id=current_user.id,
        client_id=client.id,
    ).update({Invoice.client_id: None}, synchronize_session=False)
    db.session.delete(client)
    db.session.commit()
    flash("Client removed. Existing invoices were left unchanged.", "info")
    return redirect(url_for("clients.index"))


@bp.route("/search")
@login_required
def search():
    query = (request.args.get("q") or "").strip()[:100]
    clients_query = Client.query.filter_by(user_id=current_user.id)
    if query:
        clients_query = clients_query.filter(Client.name.ilike(f"%{query}%"))
    matches = clients_query.order_by(Client.name.asc()).limit(10).all()
    return jsonify({"clients": [_client_payload(client) for client in matches]})


@bp.route("/services/new", methods=["GET", "POST"])
@login_required
def service_create():
    if request.method == "POST":
        service, error = _save_service(None)
        if service is not None:
            flash("Service item saved.", "success")
            return redirect(url_for("clients.index"))
        flash(error, "error")
    return render_template("clients/service_form.html", service=None)


@bp.route("/services/<int:service_id>/edit", methods=["GET", "POST"])
@login_required
def service_edit(service_id: int):
    service = _own_service(service_id)
    if request.method == "POST":
        saved, error = _save_service(service)
        if saved is not None:
            flash("Service item updated.", "success")
            return redirect(url_for("clients.index"))
        flash(error, "error")
    return render_template("clients/service_form.html", service=service)


@bp.route("/services/<int:service_id>/delete", methods=["POST"])
@login_required
def service_delete(service_id: int):
    service = _own_service(service_id)
    db.session.delete(service)
    db.session.commit()
    flash("Service item removed. Existing invoices were left unchanged.", "info")
    return redirect(url_for("clients.index"))


@bp.route("/defaults", methods=["GET", "POST"])
@login_required
def defaults():
    defaults_record = current_user.business_defaults
    if request.method == "POST":
        values, error = _business_default_values(request.form)
        if error:
            flash(error, "error")
        else:
            if defaults_record is None:
                defaults_record = BusinessDefaults(user_id=current_user.id)
                db.session.add(defaults_record)
            for field, value in values.items():
                setattr(defaults_record, field, value)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash(
                    "Business defaults could not be saved. Please try again.",
                    "error",
                )
            else:
                flash("Business defaults saved.", "success")
                return redirect(url_for("clients.index"))
    return render_template(
        "clients/defaults_form.html",
        defaults=defaults_record,
    )


def invoice_form_context(
    form_data=None,
    *,
    selected_client_id=None,
    selected_service_id=None,
) -> dict:
    """Build optional saved-data context for the invoice form."""
    today = date.today()
    context = {
        "today": today.isoformat(),
        "form_data": form_data,
        "clients": [],
        "service_items": [],
        "selected_client_id": None,
        "catalog_data": {"clients": [], "services": []},
    }
    if not current_user.is_authenticated:
        return context

    clients = (
        Client.query.filter_by(user_id=current_user.id)
        .order_by(Client.name.asc())
        .all()
    )
    services = (
        ServiceItem.query.filter_by(user_id=current_user.id)
        .order_by(ServiceItem.name.asc())
        .all()
    )
    defaults_record = current_user.business_defaults

    raw_client_id = selected_client_id
    if raw_client_id is None and form_data is not None:
        raw_client_id = form_data.get("client_id")
    selected_client = _client_from_raw_id(raw_client_id)
    selected_service = _service_from_raw_id(selected_service_id)

    if form_data is None:
        initial = MultiDict()
        if defaults_record is not None:
            initial.update(
                {
                    "from_company": defaults_record.from_company or "",
                    "from_address": defaults_record.from_address or "",
                    "from_email": defaults_record.from_email or "",
                    "from_phone": defaults_record.from_phone or "",
                    "notes": defaults_record.default_notes or "",
                    "payment_info": defaults_record.default_payment_info or "",
                    "payment_url": defaults_record.default_payment_url or "",
                    "tax_rate": _decimal_text(defaults_record.default_tax_rate),
                }
            )
        terms_days = (
            defaults_record.default_payment_terms_days
            if defaults_record is not None
            else 30
        )
        if selected_client is not None:
            initial.update(
                {
                    "client_id": str(selected_client.id),
                    "to_name": selected_client.name,
                    "to_address": selected_client.address or "",
                    "to_email": selected_client.email or "",
                }
            )
            if selected_client.default_tax_rate is not None:
                initial["tax_rate"] = _decimal_text(
                    selected_client.default_tax_rate
                )
            if selected_client.default_payment_terms_days is not None:
                terms_days = selected_client.default_payment_terms_days
        initial["invoice_date"] = today.isoformat()
        initial["due_date"] = (today + timedelta(days=terms_days)).isoformat()
        if selected_service is not None:
            initial.add("description[]", selected_service.description)
            initial.add(
                "qty[]",
                _decimal_text(selected_service.default_quantity),
            )
            initial.add("rate[]", _decimal_text(selected_service.default_rate))
        form_data = initial

    default_tax_rate = (
        defaults_record.default_tax_rate if defaults_record is not None else 0
    )
    default_terms = (
        defaults_record.default_payment_terms_days
        if defaults_record is not None
        else 30
    )
    context.update(
        {
            "form_data": form_data,
            "clients": clients,
            "service_items": services,
            "selected_client_id": (
                selected_client.id if selected_client is not None else None
            ),
            "catalog_data": {
                "clients": [
                    _client_payload(
                        client,
                        fallback_tax_rate=default_tax_rate,
                        fallback_terms_days=default_terms,
                    )
                    for client in clients
                ],
                "services": [
                    {
                        "id": service.id,
                        "name": service.name,
                        "description": service.description,
                        "rate": _decimal_text(service.default_rate),
                        "quantity": _decimal_text(service.default_quantity),
                    }
                    for service in services
                ],
            },
        }
    )
    return context


def selected_owned_client_id(raw_client_id) -> int | None:
    """Resolve a submitted client id without permitting cross-owner links."""
    client = _client_from_raw_id(raw_client_id)
    return client.id if client is not None else None


def _save_client(client: Client | None) -> tuple[Client | None, str | None]:
    values, error = _client_values(request.form)
    if error:
        return None, error

    duplicate = Client.query.filter_by(
        user_id=current_user.id,
        normalized_name=values["normalized_name"],
    )
    if client is not None:
        duplicate = duplicate.filter(Client.id != client.id)
    if duplicate.first() is not None:
        return None, "A client with that name already exists."

    record = client or Client(user_id=current_user.id)
    for field, value in values.items():
        setattr(record, field, value)
    db.session.add(record)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, "A client with that name already exists."
    return record, None


def _save_service(
    service: ServiceItem | None,
) -> tuple[ServiceItem | None, str | None]:
    values, error = _service_values(request.form)
    if error:
        return None, error

    duplicate = ServiceItem.query.filter_by(
        user_id=current_user.id,
        normalized_name=values["normalized_name"],
    )
    if service is not None:
        duplicate = duplicate.filter(ServiceItem.id != service.id)
    if duplicate.first() is not None:
        return None, "A service item with that name already exists."

    record = service or ServiceItem(user_id=current_user.id)
    for field, value in values.items():
        setattr(record, field, value)
    db.session.add(record)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, "A service item with that name already exists."
    return record, None


def _client_values(form) -> tuple[dict | None, str | None]:
    name, error = _short_text(form.get("name"), "Client name", required=True)
    if error:
        return None, error
    email = normalize_email(form.get("email"))
    if email and (len(email) > 200 or not is_valid_email(email)):
        return None, "Enter a valid client email address."
    address, error = _long_text(form.get("address"), "Address")
    if error:
        return None, error
    tax_rate, error = _decimal_value(
        form.get("default_tax_rate"),
        "Default tax rate",
        minimum=Decimal("0"),
        maximum=Decimal("100"),
        places=4,
        optional=True,
    )
    if error:
        return None, error
    terms_days, error = _integer_value(
        form.get("default_payment_terms_days"),
        "Payment terms",
        minimum=0,
        maximum=3650,
        optional=True,
    )
    if error:
        return None, error
    return {
        "name": name,
        "normalized_name": _normalized_name(name),
        "email": email or None,
        "address": address or None,
        "default_tax_rate": tax_rate,
        "default_payment_terms_days": terms_days,
    }, None


def _service_values(form) -> tuple[dict | None, str | None]:
    name, error = _short_text(
        form.get("name"),
        "Service name",
        required=True,
    )
    if error:
        return None, error
    description, error = _long_text(
        form.get("description"),
        "Description",
        required=True,
    )
    if error:
        return None, error
    rate, error = _decimal_value(
        form.get("default_rate"),
        "Default rate",
        minimum=Decimal("0"),
        maximum=_MAX_RATE,
        places=2,
    )
    if error:
        return None, error
    quantity, error = _decimal_value(
        form.get("default_quantity"),
        "Default quantity",
        minimum=Decimal("0.0001"),
        maximum=_MAX_QUANTITY,
        places=4,
    )
    if error:
        return None, error
    return {
        "name": name,
        "normalized_name": _normalized_name(name),
        "description": description,
        "default_rate": rate,
        "default_quantity": quantity,
    }, None


def _business_default_values(form) -> tuple[dict | None, str | None]:
    values = {}
    for field, label in (
        ("from_company", "Company name"),
        ("from_email", "Business email"),
        ("from_phone", "Phone"),
    ):
        value, error = _short_text(form.get(field), label)
        if error:
            return None, error
        values[field] = value or None

    if values["from_email"] and not is_valid_email(values["from_email"]):
        return None, "Enter a valid business email address."

    for target, source, label in (
        ("from_address", "from_address", "Business address"),
        ("default_notes", "default_notes", "Default notes"),
        (
            "default_payment_info",
            "default_payment_info",
            "Default payment info",
        ),
    ):
        value, error = _long_text(form.get(source), label)
        if error:
            return None, error
        values[target] = value or None

    try:
        values["default_payment_url"] = normalize_payment_url(
            form.get("default_payment_url")
        )
    except PaymentURLValidationError as exc:
        return None, str(exc)

    tax_rate, error = _decimal_value(
        form.get("default_tax_rate"),
        "Default tax rate",
        minimum=Decimal("0"),
        maximum=Decimal("100"),
        places=4,
    )
    if error:
        return None, error
    terms_days, error = _integer_value(
        form.get("default_payment_terms_days"),
        "Payment terms",
        minimum=0,
        maximum=3650,
    )
    if error:
        return None, error
    values["default_tax_rate"] = tax_rate
    values["default_payment_terms_days"] = terms_days
    return values, None


def _own_client(client_id: int) -> Client:
    client = Client.query.filter_by(
        id=client_id,
        user_id=current_user.id,
    ).first()
    if client is None:
        abort(404)
    return client


def _own_service(service_id: int) -> ServiceItem:
    service = ServiceItem.query.filter_by(
        id=service_id,
        user_id=current_user.id,
    ).first()
    if service is None:
        abort(404)
    return service


def _client_from_raw_id(raw_client_id) -> Client | None:
    if raw_client_id in (None, ""):
        return None
    try:
        client_id = int(raw_client_id)
    except (TypeError, ValueError):
        abort(404)
    return _own_client(client_id)


def _service_from_raw_id(raw_service_id) -> ServiceItem | None:
    if raw_service_id in (None, ""):
        return None
    try:
        service_id = int(raw_service_id)
    except (TypeError, ValueError):
        abort(404)
    return _own_service(service_id)


def _client_payload(
    client: Client,
    *,
    fallback_tax_rate=0,
    fallback_terms_days=30,
) -> dict:
    tax_rate = (
        client.default_tax_rate
        if client.default_tax_rate is not None
        else fallback_tax_rate
    )
    terms_days = (
        client.default_payment_terms_days
        if client.default_payment_terms_days is not None
        else fallback_terms_days
    )
    return {
        "id": client.id,
        "name": client.name,
        "email": client.email or "",
        "address": client.address or "",
        "tax_rate": _decimal_text(tax_rate),
        "payment_terms_days": terms_days,
    }


def _normalized_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _short_text(raw, label: str, *, required: bool = False):
    value = " ".join(str(raw or "").split())
    if required and not value:
        return None, f"{label} is required."
    if len(value) > 200:
        return None, f"{label} must be 200 characters or fewer."
    return value, None


def _long_text(raw, label: str, *, required: bool = False):
    value = str(raw or "").strip()
    if required and not value:
        return None, f"{label} is required."
    if len(value) > _MAX_TEXT:
        return None, f"{label} must be {_MAX_TEXT} characters or fewer."
    return value, None


def _decimal_value(
    raw,
    label: str,
    *,
    minimum: Decimal,
    maximum: Decimal,
    places: int,
    optional: bool = False,
):
    value = str(raw or "").strip()
    if optional and not value:
        return None, None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None, f"{label} must be a number."
    if not parsed.is_finite() or parsed < minimum or parsed > maximum:
        return None, f"{label} must be between {minimum} and {maximum}."
    if parsed.as_tuple().exponent < -places:
        return None, f"{label} cannot have more than {places} decimal places."
    return parsed, None


def _integer_value(
    raw,
    label: str,
    *,
    minimum: int,
    maximum: int,
    optional: bool = False,
):
    value = str(raw or "").strip()
    if optional and not value:
        return None, None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, f"{label} must be a whole number."
    if str(parsed) != value and value != f"+{parsed}":
        return None, f"{label} must be a whole number."
    if parsed < minimum or parsed > maximum:
        return None, f"{label} must be between {minimum} and {maximum} days."
    return parsed, None


def _decimal_text(value) -> str:
    parsed = Decimal(str(value or 0))
    return format(parsed.normalize(), "f")
