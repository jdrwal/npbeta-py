"""Tests for the user wishlist / feedback feature on the settings page."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import WishlistItem, WishlistMessage


@pytest.fixture
def user_client(db: None) -> tuple[User, Client]:
    user = User.objects.create_user(username="owner@example.com", password="pw")
    client = Client()
    client.force_login(user)
    return user, client


@pytest.mark.django_db
def test_submit_wish_creates_item(user_client: tuple[User, Client]) -> None:
    user, client = user_client
    resp = client.post(
        reverse("core:wishlist"),
        {
            "kind": WishlistItem.Kind.PROBLEM,
            "subject": "Nie działa eksport",
            "body": "Przycisk eksportu nic nie robi.",
        },
    )
    assert resp.status_code == 302
    item = WishlistItem.objects.get(user=user)
    assert item.subject == "Nie działa eksport"
    assert item.kind == WishlistItem.Kind.PROBLEM
    assert item.status == WishlistItem.Status.OPEN


@pytest.mark.django_db
def test_wishlist_lists_own_items(user_client: tuple[User, Client]) -> None:
    user, client = user_client
    WishlistItem.objects.create(user=user, subject="Ciemny motyw", body="Poproszę.")
    resp = client.get(reverse("core:wishlist"))
    assert resp.status_code == 200
    assert b"Ciemny motyw" in resp.content


@pytest.mark.django_db
def test_reply_adds_message_and_reopens(user_client: tuple[User, Client]) -> None:
    user, client = user_client
    item = WishlistItem.objects.create(
        user=user,
        subject="X",
        body="Y",
        status=WishlistItem.Status.DONE,
    )
    resp = client.post(
        reverse("core:wishlist_reply", args=[item.pk]),
        {"body": "Jednak nadal nie działa."},
    )
    assert resp.status_code == 302
    msg = WishlistMessage.objects.get(item=item)
    assert msg.body == "Jednak nadal nie działa."
    assert msg.from_staff is False
    item.refresh_from_db()
    assert item.status == WishlistItem.Status.OPEN


@pytest.mark.django_db
def test_reply_is_owner_scoped(user_client: tuple[User, Client]) -> None:
    _, client = user_client
    other = User.objects.create_user(username="other@example.com", password="pw")
    item = WishlistItem.objects.create(user=other, subject="X", body="Y")
    resp = client.post(
        reverse("core:wishlist_reply", args=[item.pk]), {"body": "hej"}
    )
    assert resp.status_code == 404
    assert WishlistMessage.objects.count() == 0
