"""Security helpers."""

from urllib.parse import urljoin, urlparse

from flask import request


def is_safe_redirect(target: str | None) -> bool:
    """Check that a redirect target stays on this site (prevents open redirects).

    Args:
        target: URL taken from e.g. the ``?next=`` query parameter.

    Returns:
        ``True`` only for http(s) URLs on the same host as the request.
    """
    if not target:
        return False
    host_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and host_url.netloc == test_url.netloc
