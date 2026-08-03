"""Template context processors for cross-cutting UI flags."""

from typing import Any

from django.conf import settings
from django.http import HttpRequest


def dev_flags(request: HttpRequest) -> dict[str, Any]:
    """Expose whether this is a development (DEBUG) instance to templates."""
    return {"is_dev": settings.DEBUG}
