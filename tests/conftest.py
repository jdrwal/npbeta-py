"""Shared pytest fixtures: run Celery tasks eagerly and capture email locally."""

from typing import Any

import pytest

from config.celery import app


@pytest.fixture(autouse=True)
def _celery_eager() -> None:
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True


@pytest.fixture(autouse=True)
def _email_locmem(settings: Any) -> None:
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
