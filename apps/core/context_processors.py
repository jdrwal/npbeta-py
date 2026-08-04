"""Template context processors for cross-cutting UI flags."""

from typing import Any

from django.conf import settings
from django.http import HttpRequest


def dev_flags(request: HttpRequest) -> dict[str, Any]:
    """Expose cross-cutting UI flags (dev instance, open registration)."""
    return {
        "is_dev": settings.DEBUG,
        "registration_open": settings.REGISTRATION_OPEN,
    }
