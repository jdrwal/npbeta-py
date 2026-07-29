"""Smoke tests: confirm the skeleton boots and core endpoints respond."""

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_healthz_ok() -> None:
    client = Client()
    response = client.get(reverse("core:healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_home_ok() -> None:
    client = Client()
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
