"""CRUD tests for rooms, contracts, records and flats (owner-scoped)."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.core.models import Contract, Flat, LedgerEntry, Room


@pytest.fixture
def setup(db: None) -> tuple:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    client = Client()
    client.force_login(user)
    flat = Flat.objects.create(owner=user, city="City", street="Street", code="X1")
    room = Room.objects.create(owner=user, flat=flat, room_no=1, beds=2, fee=500)
    return user, client, flat, room


@pytest.mark.django_db
def test_rooms_list_renders(setup: tuple) -> None:
    _, client, _, _ = setup
    assert client.get(reverse("core:rooms")).status_code == 200


@pytest.mark.django_db
def test_room_create(setup: tuple) -> None:
    user, client, flat, _ = setup
    response = client.post(
        reverse("core:add_room"),
        {"flat": flat.pk, "room_no": 2, "beds": 1, "fee": "400"},
    )
    assert response.status_code == 302
    assert Room.objects.filter(owner=user, room_no=2).exists()


@pytest.mark.django_db
def test_room_soft_delete(setup: tuple) -> None:
    _, client, _, room = setup
    response = client.post(reverse("core:delete_room", args=[room.pk]))
    assert response.status_code == 302
    assert not Room.objects.filter(pk=room.pk).exists()  # hidden by default manager
    assert Room.all_objects.get(pk=room.pk).is_deleted is True


@pytest.mark.django_db
def test_contract_create(setup: tuple) -> None:
    user, client, flat, room = setup
    response = client.post(
        reverse("core:add_contract"),
        {
            "flat": flat.pk,
            "room": room.pk,
            "contract_number": "C/1",
            "contract_start": "2026-01-01",
            "contract_end": "2026-12-31",
        },
    )
    assert response.status_code == 302
    assert Contract.objects.filter(owner=user, contract_number="C/1").exists()


@pytest.mark.django_db
def test_record_create_and_delete(setup: tuple) -> None:
    user, client, flat, _ = setup
    response = client.post(
        reverse("core:add_record"),
        {
            "flat": flat.pk,
            "short_desc": "Rent",
            "record_date": "2026-01-05",
            "amount_in_taxable": "1200",
        },
    )
    assert response.status_code == 302
    entry = LedgerEntry.objects.get(owner=user, short_desc="Rent")

    response = client.post(reverse("core:delete_record", args=[entry.pk]))
    assert response.status_code == 302
    assert not LedgerEntry.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_flat_soft_delete(setup: tuple) -> None:
    _, client, flat, _ = setup
    response = client.post(reverse("core:delete_flat", args=[flat.pk]))
    assert response.status_code == 302
    assert Flat.all_objects.get(pk=flat.pk).is_deleted is True


@pytest.mark.django_db
def test_cannot_edit_other_users_flat(setup: tuple) -> None:
    _, client, _, _ = setup
    other = get_user_model().objects.create_user(username="other@example.com", password="pw")
    other_flat = Flat.objects.create(owner=other, city="O", street="O", code="O1")
    assert client.get(reverse("core:edit_flat", args=[other_flat.pk])).status_code == 404
