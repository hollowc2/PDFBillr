"""Reply-To addresses for outgoing mail.

Mail is sent from MAIL_DEFAULT_SENDER (usually a no-reply address), so every
email that invites a reply sets Reply-To explicitly.
"""

from flask import current_app

from utils.validation import is_valid_email


def invoice_reply_to(invoice) -> str | None:
    """Replies to invoice and reminder emails go to the business that sent them."""
    for candidate in (invoice.from_email, invoice.user.email if invoice.user else None):
        if candidate and is_valid_email(candidate.strip()):
            return candidate.strip()
    return None


def support_reply_to() -> str | None:
    """Replies to account emails go to SUPPORT_EMAIL when one is configured."""
    address = (current_app.config.get("SUPPORT_EMAIL") or "").strip()
    return address if address and is_valid_email(address) else None
