"""Custom middleware for the accounts app."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect


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


class RoleAccessMiddleware:
    """Keep landlords and tenants on their own side of the app.

    Tenants may only reach the tenant portal and their own account pages; every
    other authenticated path redirects them to the portal. Landlords are kept
    out of the tenant portal. Anonymous requests and static assets pass through.
    """

    # Path prefixes a tenant account is allowed to visit.
    TENANT_PREFIXES = ("/portal", "/settings", "/logout", "/prywatnosc")
    SKIP_PREFIXES = ("/static", "/media")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        path = request.path
        if (
            user is not None
            and user.is_authenticated
            and not path.startswith(self.SKIP_PREFIXES)
        ):
            if getattr(user, "is_tenant", False):
                if not path.startswith(self.TENANT_PREFIXES):
                    return redirect("core:portal")
            elif getattr(user, "is_landlord", False):
                if path.startswith("/portal"):
                    return redirect("core:dashboard")
        return self.get_response(request)
