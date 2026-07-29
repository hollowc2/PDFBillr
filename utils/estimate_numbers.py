"""Owner-scoped estimate-number helpers."""

from __future__ import annotations

from models import Estimate


MAX_ESTIMATE_NUMBER_LENGTH = 200


def estimate_number_exists(
    user_id: int,
    estimate_number: str,
    *,
    exclude_id: int | None = None,
) -> bool:
    query = Estimate.query.filter_by(
        user_id=user_id,
        estimate_number=estimate_number,
    )
    if exclude_id is not None:
        query = query.filter(Estimate.id != exclude_id)
    return query.with_entities(Estimate.id).first() is not None


def next_available_estimate_number(user_id: int, preferred: str = "EST-001") -> str:
    normalized = preferred.strip()[:MAX_ESTIMATE_NUMBER_LENGTH] or "EST"
    if not estimate_number_exists(user_id, normalized):
        return normalized

    sequence = 2
    while True:
        suffix = f"-{sequence}"
        candidate = (
            f"{normalized[: MAX_ESTIMATE_NUMBER_LENGTH - len(suffix)]}{suffix}"
        )
        if not estimate_number_exists(user_id, candidate):
            return candidate
        sequence += 1
