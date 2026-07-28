"""Canonical external URL generation.

Outbound email and Stripe URLs must never trust a request-supplied Host or
forwarded-host header in production.
"""

from urllib.parse import urlsplit, urlunsplit

from flask import current_app, has_request_context, url_for


def external_url(endpoint: str, **values) -> str:
    """Build an endpoint URL beneath the configured canonical public base."""
    base = current_app.config.get("PUBLIC_BASE_URL", "")
    if has_request_context():
        path = url_for(endpoint, _external=False, **values)
    elif base:
        parsed = urlsplit(base)
        adapter = current_app.url_map.bind(
            parsed.netloc,
            script_name=parsed.path or "/",
            url_scheme=parsed.scheme,
        )
        path = adapter.build(endpoint, values, force_external=False)
    else:
        return url_for(endpoint, _external=True, **values)

    if not base:
        return url_for(endpoint, _external=True, **values)

    parsed = urlsplit(base)
    base_path = parsed.path.rstrip("/")
    if base_path and (path == base_path or path.startswith(f"{base_path}/")):
        final_path = path
    else:
        final_path = f"{base_path}/{path.lstrip('/')}"
    return urlunsplit((parsed.scheme, parsed.netloc, final_path, "", ""))
