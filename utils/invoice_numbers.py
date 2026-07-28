"""Invoice-number uniqueness helpers.

User-entered invoice numbers should be rejected when already in use. Automated
flows such as duplicate and recurring generation may use
``next_available_invoice_number`` to choose an explicit suffix.
"""

from __future__ import annotations

from models import Invoice


MAX_INVOICE_NUMBER_LENGTH = 200


def invoice_number_exists(
    user_id: int,
    invoice_number: str,
    *,
    exclude_id: int | None = None,
) -> bool:
    query = Invoice.query.filter_by(
        user_id=user_id,
        invoice_number=invoice_number,
    )
    if exclude_id is not None:
        query = query.filter(Invoice.id != exclude_id)
    return query.with_entities(Invoice.id).first() is not None


def next_available_invoice_number(user_id: int, preferred: str) -> str:
    """Return ``preferred`` or a deterministic ``-N`` suffixed alternative."""
    normalized = preferred.strip()[:MAX_INVOICE_NUMBER_LENGTH] or "INV"
    if not invoice_number_exists(user_id, normalized):
        return normalized

    sequence = 2
    while True:
        suffix = f"-{sequence}"
        candidate = f"{normalized[: MAX_INVOICE_NUMBER_LENGTH - len(suffix)]}{suffix}"
        if not invoice_number_exists(user_id, candidate):
            return candidate
        sequence += 1
