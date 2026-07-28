from datetime import datetime, timezone

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
    branding = db.relationship("BrandingProfile", back_populates="user", uselist=False)

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


class Invoice(db.Model):
    __tablename__ = "invoices"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "invoice_number",
            name="uq_invoices_user_id_invoice_number",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    invoice_number = db.Column(db.String(200), nullable=False)
    invoice_date = db.Column(db.String(50), nullable=True)
    due_date = db.Column(db.String(50), nullable=True)

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

    notes = db.Column(db.Text, nullable=True)
    payment_info = db.Column(db.Text, nullable=True)
    logo_filename = db.Column(db.String(255), nullable=True)
    theme = db.Column(db.String(50), default="default")

    # status: draft | sent | finalized
    status = db.Column(db.String(20), default="draft")
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
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

    user = db.relationship("User", back_populates="invoices")


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

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Invoice template fields
    invoice_number_prefix = db.Column(db.String(100), nullable=True)
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
    notes         = db.Column(db.Text, nullable=True)
    payment_info  = db.Column(db.Text, nullable=True)
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
