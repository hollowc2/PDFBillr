from datetime import datetime, timezone
from functools import wraps

from flask import flash, g, redirect, url_for
from flask_login import current_user


def _compute_is_pro(u) -> bool:
    if not u or not u.is_authenticated:
        return False
    sub = u.subscription
    if not sub or sub.status not in ("active", "trialing"):
        return False
    if sub.current_period_end:
        period_end = sub.current_period_end
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=timezone.utc)
        if period_end < datetime.now(timezone.utc):
            return False
    return True


def is_pro(user=None) -> bool:
    if user is not None:
        # Explicit user arg (e.g. webhook handlers) — bypass cache
        return _compute_is_pro(user)
    # Cache per-request on Flask g to avoid repeated DB hits
    if not hasattr(g, '_is_pro'):
        g._is_pro = _compute_is_pro(current_user)
    return g._is_pro


def pro_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_pro():
            flash("This feature requires PDFBillr Pro.", "warning")
            return redirect(url_for("billing.upgrade"))
        return f(*args, **kwargs)
    return decorated
