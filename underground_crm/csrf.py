"""
A CSRF failure view that records why a request was refused.

Django's own failure view renders a 403 page and logs only that a request was refused.
The donation form posts JSON and reports any reply it cannot parse as a network error,
so the refusal's reason is otherwise invisible to everybody involved.
"""

import logging

from django.http import HttpRequest, HttpResponse
from django.views.csrf import csrf_failure

logger = logging.getLogger(__name__)

_COOKIE_NAME = "csrftoken"


def count_cookies_named(raw_cookie_header: str, name: str) -> int:
    """
    How many cookies of this name the browser sent.

    A browser sends every cookie matching the request, and Django's parser silently keeps
    only one of any that share a name. An old cookie scoped to the parent domain sitting
    beside a host-only one is therefore invisible to Django, yet can be the one it keeps.
    """
    pairs = (pair.strip() for pair in raw_cookie_header.split(";"))
    return sum(1 for pair in pairs if pair.split("=", 1)[0] == name)


def logged_csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    raw_cookie_header = request.META.get("HTTP_COOKIE", "")
    logger.warning(
        "CSRF refusal for %s %s: reason=%r; csrftoken cookies sent=%d; "
        "X-CSRFToken header present=%s; Origin=%r; Referer=%r; User-Agent=%r",
        request.method,
        request.path,
        reason,
        count_cookies_named(raw_cookie_header, _COOKIE_NAME),
        bool(request.META.get("HTTP_X_CSRFTOKEN")),
        request.META.get("HTTP_ORIGIN"),
        request.META.get("HTTP_REFERER"),
        request.META.get("HTTP_USER_AGENT"),
    )
    return csrf_failure(request, reason=reason)
