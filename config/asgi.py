"""ASGI config for the Rozlicz Najem project (for future async/websocket needs)."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
