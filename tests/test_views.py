"""Smoke tests for the authenticated list views."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse


@pytest.fixture
def client_logged_in(db: None) -> Client:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name",
    ["core:flats", "core:contracts", "core:records", "core:calculations", "core:arrears"],
)
def test_list_views_render(client_logged_in: Client, url_name: str) -> None:
    response = client_logged_in.get(reverse(url_name))
    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name",
    ["core:flats", "core:contracts", "core:records", "core:calculations", "core:arrears"],
)
def test_list_views_require_login(url_name: str) -> None:
    response = Client().get(reverse(url_name))
    assert response.status_code == 302
    assert reverse("login") in response.headers["Location"]
