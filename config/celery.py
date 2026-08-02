"""Celery application for background jobs (fee calculations, emails)."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("rozlicz_najem")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
