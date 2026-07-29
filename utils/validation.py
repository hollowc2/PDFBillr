"""Small server-side validators for security-sensitive user input."""

from __future__ import annotations

import re
from ipaddress import ip_address
from urllib.parse import urlsplit

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_PAYMENT_URL_LENGTH = 2048


class PaymentURLValidationError(ValueError):
    """Raised when a merchant-supplied payment link is unsafe."""


def normalize_email(raw) -> str:
    return str(raw or "").strip().lower()


def is_valid_email(value: str) -> bool:
    return (
        3 <= len(value) <= 254
        and "\r" not in value
        and "\n" not in value
        and _EMAIL_RE.fullmatch(value) is not None
    )


def normalize_payment_url(raw) -> str | None:
    """Validate an optional browser-navigation-only HTTPS payment URL."""
    value = str(raw or "").strip()
    if not value:
        return None
    if len(value) > MAX_PAYMENT_URL_LENGTH:
        raise PaymentURLValidationError(
            f"Payment link must be {MAX_PAYMENT_URL_LENGTH} characters or fewer."
        )
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise PaymentURLValidationError("Payment link cannot contain whitespace.")
    if "\\" in value:
        raise PaymentURLValidationError("Payment link contains an invalid character.")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise PaymentURLValidationError("Enter a valid HTTPS payment link.") from None

    if parsed.scheme.lower() != "https":
        raise PaymentURLValidationError("Payment link must use HTTPS.")
    if not parsed.netloc or not parsed.hostname:
        raise PaymentURLValidationError("Payment link must include a valid host.")
    if parsed.username is not None or parsed.password is not None:
        raise PaymentURLValidationError(
            "Payment link cannot include username or password credentials."
        )
    if port is not None and not 1 <= port <= 65535:
        raise PaymentURLValidationError("Payment link contains an invalid port.")

    host = parsed.hostname.rstrip(".").lower()
    if not host:
        raise PaymentURLValidationError("Payment link must include a valid host.")
    try:
        address = ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise PaymentURLValidationError(
                "Payment link must include a valid host."
            ) from None
        labels = ascii_host.split(".")
        if (
            len(ascii_host) > 253
            or len(labels) < 2
            or any(not _HOST_LABEL_RE.fullmatch(label) for label in labels)
        ):
            raise PaymentURLValidationError(
                "Payment link must include a valid public host."
            ) from None
    else:
        if not address.is_global:
            raise PaymentURLValidationError(
                "Payment link must use a public host."
            )

    return value
