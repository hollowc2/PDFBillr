"""Small server-side validators for security-sensitive user input."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw) -> str:
    return str(raw or "").strip().lower()


def is_valid_email(value: str) -> bool:
    return (
        3 <= len(value) <= 254
        and "\r" not in value
        and "\n" not in value
        and _EMAIL_RE.fullmatch(value) is not None
    )
