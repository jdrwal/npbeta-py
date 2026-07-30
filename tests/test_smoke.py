"""Smoke tests: confirm the skeleton boots and core endpoints respond."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_healthz_ok() -> None:
    client = Client()
    response = client.get(reverse("core:healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_dashboard_requires_login() -> None:
    client = Client()
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 302
    assert reverse("login") in response.headers["Location"]


@pytest.mark.django_db
def test_dashboard_ok_when_logged_in() -> None:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 200
