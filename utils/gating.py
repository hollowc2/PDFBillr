from datetime import datetime, timezone
from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user


def is_pro(user=None) -> bool:
    u = user or current_user
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


def pro_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_pro():
            flash("This feature requires PDFBillr Pro.", "warning")
            return redirect(url_for("billing.upgrade"))
        return f(*args, **kwargs)
    return decorated
