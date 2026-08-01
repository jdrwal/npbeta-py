"""Custom middleware for the accounts app."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


class SessionTimeoutMiddleware:
    """Enforce a per-user inactivity logout window.

    ``SESSION_COOKIE_AGE`` is a project-wide default; this middleware overrides
    it on every authenticated request using the user's configured
    ``session_timeout_minutes``. Combined with ``SESSION_SAVE_EVERY_REQUEST``
    this yields a sliding-expiration inactivity logout.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            minutes = getattr(user, "session_timeout_minutes", 30) or 30
            request.session.set_expiry(minutes * 60)
        return self.get_response(request)
