from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def _now():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    auth_session_version = db.Column(
        db.Integer, nullable=False, default=1, server_default="1"
    )
    created_at = db.Column(db.DateTime(timezone=True), default=_now)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    stripe_customer_id = db.Column(db.String(255), nullable=True)

    subscription = db.relationship("Subscription", back_populates="user", uselist=False)
    invoices = db.relationship("Invoice", back_populates="user", lazy="dynamic")
    estimates = db.relationship(
        "Estimate",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    branding = db.relationship("BrandingProfile", back_populates="user", uselist=False)
    business_defaults = db.relationship(
        "BusinessDefaults",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    clients = db.relationship(
        "Client",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    service_items = db.relationship(
        "ServiceItem",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    reminder_preference = db.relationship(
        "ReminderPreference",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def set_password(self, password: str) -> None:
        had_password = bool(self.password_hash)
        self.password_hash = generate_password_hash(password)
        if had_password:
            self.auth_session_version = (self.auth_session_version or 1) + 1

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_id(self) -> str:
        """Bind Flask-Login sessions and remember cookies to credential state."""
        return f"{self.id}:{self.auth_session_version or 1}"


class Subscription(db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    plan = db.Column(db.String(20), default="free", nullable=False)
    stripe_sub_id = db.Column(db.String(255), nullable=True)
    stripe_price_id = db.Column(db.String(255), nullable=True)
    # status: active | past_due | canceled | trialing
    status = db.Column(db.String(20), default="active", nullable=False)
    current_period_end = db.Column(db.DateTime(timezone=True), nullable=True)
    last_stripe_event_created = db.Column(db.BigInteger, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_now)
    updated_at = db.Column(db.DateTime(timezone=True), default=_now, onupdate=_now)

    user = db.relationship("User", back_populates="subscription")


class BusinessDefaults(db.Model):
    """Reusable sender and invoice defaults for one account."""

    __tablename__ = "business_defaults"
    __table_args__ = (
        db.CheckConstraint(
            "default_tax_rate >= 0 AND default_tax_rate <= 100",
            name="ck_business_defaults_tax_rate_range",
        ),
        db.CheckConstraint(
            "default_payment_terms_days >= 0 "
            "AND default_payment_terms_days <= 3650",
            name="ck_business_defaults_payment_terms_range",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    from_company = db.Column(db.String(200), nullable=True)
    from_address = db.Column(db.Text, nullable=True)
    from_email = db.Column(db.String(200), nullable=True)
    from_phone = db.Column(db.String(200), nullable=True)
    default_notes = db.Column(db.Text, nullable=True)
    default_payment_info = db.Column(db.Text, nullable=True)
    default_payment_url = db.Column(db.String(2048), nullable=True)
    default_tax_rate = db.Column(
        db.Numeric(7, 4), nullable=False, default=0, server_default="0"
    )
    default_payment_terms_days = db.Column(
        db.Integer, nullable=False, default=30, server_default="30"
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user = db.relationship("User", back_populates="business_defaults")


class Client(db.Model):
    """An owner-scoped reusable billing contact."""

    __tablename__ = "clients"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_clients_user_id_normalized_name",
        ),
        db.CheckConstraint(
            "default_tax_rate IS NULL "
            "OR (default_tax_rate >= 0 AND default_tax_rate <= 100)",
            name="ck_clients_tax_rate_range",
        ),
        db.CheckConstraint(
            "default_payment_terms_days IS NULL "
            "OR (default_payment_terms_days >= 0 "
            "AND default_payment_terms_days <= 3650)",
            name="ck_clients_payment_terms_range",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    normalized_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=True)
    address = db.Column(db.Text, nullable=True)
    default_tax_rate = db.Column(db.Numeric(7, 4), nullable=True)
    default_payment_terms_days = db.Column(db.Integer, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user = db.relationship("User", back_populates="clients")
    invoices = db.relationship(
        "Invoice",
        back_populates="client",
        passive_deletes=True,
    )
    estimates = db.relationship(
        "Estimate",
        back_populates="client",
        passive_deletes=True,
    )


class ServiceItem(db.Model):
    """An owner-scoped reusable service or product."""

    __tablename__ = "service_items"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_service_items_user_id_normalized_name",
        ),
        db.CheckConstraint(
            "default_rate >= 0",
            name="ck_service_items_default_rate_nonnegative",
        ),
        db.CheckConstraint(
            "default_quantity > 0",
            name="ck_service_items_default_quantity_positive",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    normalized_name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    default_rate = db.Column(db.Numeric(18, 2), nullable=False)
    default_quantity = db.Column(
        db.Numeric(18, 4), nullable=False, default=1, server_default="1"
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user = db.relationship("User", back_populates="service_items")


class Invoice(db.Model):
    __tablename__ = "invoices"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "invoice_number",
            name="uq_invoices_user_id_invoice_number",
        ),
        db.CheckConstraint(
            "currency_code IN ('USD', 'CAD', 'EUR', 'GBP', 'AUD', 'JPY')",
            name="ck_invoices_currency_code",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    client_id = db.Column(
        db.Integer,
        db.ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    invoice_number = db.Column(db.String(200), nullable=False)
    currency_code = db.Column(
        db.String(3), nullable=False, default="USD", server_default="USD"
    )
    invoice_date = db.Column(db.String(50), nullable=True)
    due_date = db.Column(db.String(50), nullable=True)
    # Nullable typed shadows support a staged, reversible data migration.
    # Legacy columns remain authoritative until an explicit read-path switch.
    invoice_date_value = db.Column(db.Date, nullable=True)
    due_date_value = db.Column(db.Date, nullable=True)

    from_company = db.Column(db.String(200), nullable=True)
    from_address = db.Column(db.Text, nullable=True)
    from_email = db.Column(db.String(200), nullable=True)
    from_phone = db.Column(db.String(200), nullable=True)

    to_name = db.Column(db.String(200), nullable=True)
    to_address = db.Column(db.Text, nullable=True)
    to_email = db.Column(db.String(200), nullable=True)

    # JSON-encoded list of {"description", "qty", "rate", "amount"}
    line_items_json = db.Column(db.Text, nullable=True)

    tax_rate = db.Column(db.Float, default=0.0)
    discount = db.Column(db.Float, default=0.0)
    subtotal = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)
    tax_rate_decimal = db.Column(db.Numeric(7, 4), nullable=True)
    discount_decimal = db.Column(db.Numeric(18, 2), nullable=True)
    subtotal_decimal = db.Column(db.Numeric(18, 2), nullable=True)
    total_decimal = db.Column(db.Numeric(18, 2), nullable=True)

    notes = db.Column(db.Text, nullable=True)
    payment_info = db.Column(db.Text, nullable=True)
    payment_url = db.Column(db.String(2048), nullable=True)
    logo_filename = db.Column(db.String(255), nullable=True)
    theme = db.Column(db.String(50), default="default")

    # status: draft | sent | finalized | partial | paid | void
    status = db.Column(db.String(20), default="draft")
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)
    voided_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_now)
    updated_at = db.Column(db.DateTime(timezone=True), default=_now, onupdate=_now)

    # View tracking
    view_token = db.Column(db.String(64), unique=True, nullable=True, index=True)
    viewed_at  = db.Column(db.DateTime(timezone=True), nullable=True)
    view_count = db.Column(db.Integer, default=0)

    # Payment reminder tracking (Pro)
    reminder_3d_sent = db.Column(db.Boolean, default=False)  # 3 days before due
    reminder_0d_sent = db.Column(db.Boolean, default=False)  # on due date
    reminder_7d_sent = db.Column(db.Boolean, default=False)  # 7 days overdue
    payment_reminders_enabled = db.Column(
        db.Boolean,
        default=True,
        server_default=db.true(),
        nullable=False,
    )

    user = db.relationship("User", back_populates="invoices")
    client = db.relationship("Client", back_populates="invoices")
    payments = db.relationship(
        "InvoicePayment",
        back_populates="invoice",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="InvoicePayment.paid_at.desc(), InvoicePayment.id.desc()",
    )

    @property
    def total_amount(self) -> Decimal:
        """Return the invoice total as a currency-safe Decimal."""
        source = self.total_decimal if self.total_decimal is not None else self.total
        try:
            return Decimal(str(source or 0)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0.00")

    @property
    def amount_paid(self) -> Decimal:
        """Return the sum of recorded payments."""
        return sum(
            (payment.amount for payment in self.payments),
            start=Decimal("0.00"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def balance_due(self) -> Decimal:
        """Return the unpaid amount, never a negative number."""
        return max(
            self.total_amount - self.amount_paid,
            Decimal("0.00"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def due_date_as_date(self) -> date | None:
        """Return the typed due date with a legacy-string fallback."""
        if self.due_date_value is not None:
            return self.due_date_value
        try:
            return datetime.strptime(self.due_date or "", "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    def effective_status(self, *, as_of: date | None = None) -> str:
        """Return the user-facing state, deriving overdue from the due date."""
        if self.status in {"void", "paid"}:
            return self.status
        if (
            (
                self.status in {"sent", "finalized"}
                or (self.status == "partial" and self.sent_at is not None)
            )
            and self.balance_due > 0
            and self.due_date_as_date is not None
            and self.due_date_as_date < (as_of or date.today())
        ):
            return "overdue"
        return self.status or "draft"

    @property
    def display_status(self) -> str:
        return self.effective_status()

    @property
    def accepts_payments(self) -> bool:
        return self.status not in {"paid", "void"} and self.balance_due > 0

    def sync_payment_status(self, *, changed_at: datetime | None = None) -> None:
        """Keep the persisted lifecycle state consistent with payment rows."""
        if self.status == "void":
            return

        paid = self.amount_paid
        if self.total_amount <= 0 or paid >= self.total_amount:
            self.status = "paid"
            self.paid_at = changed_at or self.paid_at or _now()
        elif paid > 0:
            self.status = "partial"
            self.paid_at = None
        elif self.status in {"paid", "partial"}:
            self.status = "sent" if self.sent_at else "draft"
            self.paid_at = None


class Estimate(db.Model):
    """An owner-scoped, shareable estimate with immutable financial snapshots."""

    __tablename__ = "estimates"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "estimate_number",
            name="uq_estimates_user_id_estimate_number",
        ),
        db.CheckConstraint(
            "currency_code IN ('USD', 'CAD', 'EUR', 'GBP', 'AUD', 'JPY')",
            name="ck_estimates_currency_code",
        ),
        db.CheckConstraint(
            "status IN ('draft', 'sent', 'accepted', 'declined', "
            "'expired', 'converted')",
            name="ck_estimates_status",
        ),
        db.CheckConstraint(
            "expiry_date >= issue_date",
            name="ck_estimates_date_order",
        ),
        db.CheckConstraint(
            "tax_rate >= 0 AND tax_rate <= 100",
            name="ck_estimates_tax_rate_range",
        ),
        db.CheckConstraint(
            "discount >= 0 AND subtotal >= 0 AND total >= 0",
            name="ck_estimates_amounts_nonnegative",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id = db.Column(
        db.Integer,
        db.ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    converted_invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("invoices.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )

    estimate_number = db.Column(db.String(200), nullable=False)
    public_token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    status = db.Column(
        db.String(20), nullable=False, default="draft", server_default="draft"
    )
    issue_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=False)
    currency_code = db.Column(
        db.String(3), nullable=False, default="USD", server_default="USD"
    )

    from_company = db.Column(db.String(200), nullable=True)
    from_address = db.Column(db.Text, nullable=True)
    from_email = db.Column(db.String(200), nullable=True)
    from_phone = db.Column(db.String(200), nullable=True)
    to_name = db.Column(db.String(200), nullable=False)
    to_address = db.Column(db.Text, nullable=True)
    to_email = db.Column(db.String(200), nullable=True)

    line_items_json = db.Column(db.Text, nullable=False)
    tax_rate = db.Column(
        db.Numeric(7, 4), nullable=False, default=0, server_default="0"
    )
    discount = db.Column(
        db.Numeric(18, 2), nullable=False, default=0, server_default="0"
    )
    subtotal = db.Column(
        db.Numeric(18, 2), nullable=False, default=0, server_default="0"
    )
    total = db.Column(
        db.Numeric(18, 2), nullable=False, default=0, server_default="0"
    )
    notes = db.Column(db.Text, nullable=True)
    payment_info = db.Column(db.Text, nullable=True)
    client_comment = db.Column(db.Text, nullable=True)

    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    responded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    converted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user = db.relationship("User", back_populates="estimates")
    client = db.relationship("Client", back_populates="estimates")
    converted_invoice = db.relationship("Invoice", foreign_keys=[converted_invoice_id])

    @property
    def total_amount(self) -> Decimal:
        try:
            return Decimal(str(self.total or 0)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0.00")

    def effective_status(self, *, as_of: date | None = None) -> str:
        if (
            self.status == "sent"
            and self.expiry_date is not None
            and self.expiry_date < (as_of or date.today())
        ):
            return "expired"
        return self.status or "draft"

    @property
    def display_status(self) -> str:
        return self.effective_status()

    @property
    def is_terminal(self) -> bool:
        return self.effective_status() in {
            "accepted",
            "declined",
            "expired",
            "converted",
        }


class InvoicePayment(db.Model):
    """A manually recorded payment applied to an invoice."""

    __tablename__ = "invoice_payments"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_invoice_payments_amount_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    method = db.Column(db.String(50), nullable=True)
    reference = db.Column(db.String(200), nullable=True)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_now
    )

    invoice = db.relationship("Invoice", back_populates="payments")


class ReminderPreference(db.Model):
    """Per-user payment reminder schedule."""

    __tablename__ = "reminder_preferences"
    __table_args__ = (
        db.CheckConstraint(
            "before_due_days IS NULL "
            "OR (before_due_days >= 1 AND before_due_days <= 30)",
            name="ck_reminder_preferences_before_due_days",
        ),
        db.CheckConstraint(
            "overdue_days IS NULL OR (overdue_days >= 1 AND overdue_days <= 90)",
            name="ck_reminder_preferences_overdue_days",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    enabled = db.Column(
        db.Boolean,
        default=True,
        server_default=db.true(),
        nullable=False,
    )
    before_due_days = db.Column(
        db.Integer,
        default=3,
        server_default="3",
        nullable=True,
    )
    on_due_date = db.Column(
        db.Boolean,
        default=True,
        server_default=db.true(),
        nullable=False,
    )
    overdue_days = db.Column(
        db.Integer,
        default=7,
        server_default="7",
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=_now,
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=_now,
        onupdate=_now,
        nullable=False,
    )

    user = db.relationship("User", back_populates="reminder_preference")


class ProcessedStripeEvent(db.Model):
    """Tracks processed Stripe event IDs to ensure webhook idempotency."""
    __tablename__ = "processed_stripe_events"

    stripe_event_id = db.Column(db.String(255), primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_now)


class BillingNotificationDelivery(db.Model):
    """Retryable notification created atomically with a Stripe event."""

    __tablename__ = "billing_notification_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    stripe_event_id = db.Column(
        db.String(255),
        db.ForeignKey(
            "processed_stripe_events.stripe_event_id",
            ondelete="CASCADE",
        ),
        unique=True,
        nullable=False,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    template = db.Column(db.String(100), nullable=False)
    status = db.Column(
        db.String(20),
        default="pending",
        server_default="pending",
        nullable=False,
        index=True,
    )
    attempt_count = db.Column(
        db.Integer, default=0, server_default="0", nullable=False
    )
    last_attempt_at = db.Column(db.DateTime(timezone=True), nullable=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    user = db.relationship("User")


class RecurringInvoice(db.Model):
    __tablename__ = "recurring_invoices"
    __table_args__ = (
        db.CheckConstraint(
            "currency_code IN ('USD', 'CAD', 'EUR', 'GBP', 'AUD', 'JPY')",
            name="ck_recurring_invoices_currency_code",
        ),
    )

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Invoice template fields
    invoice_number_prefix = db.Column(db.String(100), nullable=True)
    currency_code = db.Column(
        db.String(3), nullable=False, default="USD", server_default="USD"
    )
    from_company  = db.Column(db.String(200), nullable=True)
    from_address  = db.Column(db.Text, nullable=True)
    from_email    = db.Column(db.String(200), nullable=True)
    from_phone    = db.Column(db.String(200), nullable=True)
    to_name       = db.Column(db.String(200), nullable=True)
    to_address    = db.Column(db.Text, nullable=True)
    to_email      = db.Column(db.String(200), nullable=True)
    line_items_json = db.Column(db.Text, nullable=True)
    tax_rate      = db.Column(db.Float, default=0.0)
    discount      = db.Column(db.Float, default=0.0)
    tax_rate_decimal = db.Column(db.Numeric(7, 4), nullable=True)
    discount_decimal = db.Column(db.Numeric(18, 2), nullable=True)
    notes         = db.Column(db.Text, nullable=True)
    payment_info  = db.Column(db.Text, nullable=True)
    payment_url   = db.Column(db.String(2048), nullable=True)
    theme         = db.Column(db.String(50), default="default")

    # Schedule — interval: monthly | weekly | biweekly | quarterly
    interval      = db.Column(db.String(20), nullable=False, default="monthly")
    net_days      = db.Column(db.Integer, default=30)  # days until due on generated invoice
    next_run_date = db.Column(db.Date, nullable=False)
    last_run_date = db.Column(db.Date, nullable=True)
    auto_send     = db.Column(db.Boolean, default=False)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime(timezone=True), default=_now)

    user = db.relationship("User", backref="recurring_invoices")


class RecurringOccurrence(db.Model):
    """Durable claim for one scheduled recurring-invoice occurrence."""

    __tablename__ = "recurring_occurrences"
    __table_args__ = (
        db.UniqueConstraint(
            "recurring_invoice_id",
            "scheduled_for",
            name="uq_recurring_occurrences_template_schedule",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    recurring_invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("recurring_invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    scheduled_for = db.Column(db.Date, nullable=False, index=True)
    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("invoices.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)

    recurring_invoice = db.relationship("RecurringInvoice")
    invoice = db.relationship("Invoice")


class InvoiceDelivery(db.Model):
    """Retryable delivery state for scheduler-generated invoice email."""

    __tablename__ = "invoice_deliveries"
    __table_args__ = (
        db.UniqueConstraint(
            "invoice_id",
            "delivery_kind",
            name="uq_invoice_deliveries_invoice_kind",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    delivery_kind = db.Column(db.String(32), nullable=False)
    status = db.Column(
        db.String(20),
        default="pending",
        server_default="pending",
        nullable=False,
        index=True,
    )
    attempt_count = db.Column(
        db.Integer, default=0, server_default="0", nullable=False
    )
    last_attempt_at = db.Column(db.DateTime(timezone=True), nullable=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    invoice = db.relationship("Invoice")


class BrandingProfile(db.Model):
    __tablename__ = "branding_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    logo_filename = db.Column(db.String(255), nullable=True)
    accent_color = db.Column(db.String(20), default="#1e3a8a")
    font_choice = db.Column(db.String(50), default="default")
    remove_footer = db.Column(db.Boolean, default=False)

    user = db.relationship("User", back_populates="branding")
