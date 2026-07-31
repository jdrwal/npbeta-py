"""CRUD tests for rooms, contracts, records and flats (owner-scoped)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import (
    AdminFee,
    AdminFeePrice,
    Contract,
    Flat,
    LedgerEntry,
    MeterDefinition,
    MeterPrice,
    Room,
)


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
            "kind": "rent",
            "short_desc": "Rent",
            "record_date": "2026-01-05",
            "billing_period": "2026-01",
            "amount_in_taxable": "1200",
        },
    )
    assert response.status_code == 302
    entry = LedgerEntry.objects.get(owner=user, short_desc="Rent")
    assert entry.billing_period.isoformat() == "2026-01-01"

    response = client.post(reverse("core:delete_record", args=[entry.pk]))
    assert response.status_code == 302
    assert not LedgerEntry.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_records_splits_rent_and_fees(setup: tuple) -> None:
    """Fees are tracked separately and must not inflate rental income."""
    user, client, flat, _ = setup
    bp = date(2026, 3, 1)
    LedgerEntry.objects.create(
        owner=user, flat=flat, short_desc="Czynsz", billing_period=bp,
        record_date=timezone.make_aware(datetime(2026, 3, 5)),
        amount_in_taxable=Decimal("1000"), kind=LedgerEntry.Kind.RENT,
    )
    LedgerEntry.objects.create(
        owner=user, flat=flat, short_desc="Media", billing_period=bp,
        record_date=timezone.make_aware(datetime(2026, 3, 6)),
        amount_in_taxable=Decimal("250"), kind=LedgerEntry.Kind.FEE,
    )
    r = client.get(reverse("core:records"), {"year": 2026, "month": 3})
    assert r.status_code == 200
    assert len(r.context["rent_entries"]) == 1
    assert len(r.context["fee_entries"]) == 1
    # Fee payment must NOT count towards taxable rental income.
    assert r.context["totals"]["income"] == Decimal("1000")


@pytest.mark.django_db
def test_flat_fees_management(setup: tuple) -> None:
    user, client, flat, _ = setup
    assert client.get(reverse("core:flat_fees", args=[flat.pk])).status_code == 200

    # Add an admin fee via the unified form.
    r = client.post(
        reverse("core:fee_add"),
        {
            "flat": flat.pk,
            "kind": "admin",
            "title": "Internet",
            "amount": "60",
            "price_date": "2026-01-01",
        },
    )
    assert r.status_code == 302
    fee = AdminFee.objects.get(flat=flat, title="Internet")
    assert fee.owner_id == user.pk
    assert AdminFeePrice.objects.filter(admin_fee=fee, price=Decimal("60")).exists()

    # Add a metered utility via the same form.
    r = client.post(
        reverse("core:fee_add"),
        {
            "flat": flat.pk,
            "kind": "kWh",
            "title": "Prąd",
            "amount": "0.85",
            "price_date": "2026-01-01",
        },
    )
    assert r.status_code == 302
    meter = MeterDefinition.objects.get(flat=flat, name="Prąd")
    assert meter.unit == "kWh"
    assert MeterPrice.objects.filter(meter=meter).exists()

    # New dated prices keep the history.
    client.post(
        reverse("core:add_fee_price", args=[flat.pk, fee.pk]),
        {"price": "70", "price_date": "2026-06-01"},
    )
    assert AdminFeePrice.objects.filter(admin_fee=fee).count() == 2
    client.post(
        reverse("core:add_meter_price", args=[flat.pk, meter.pk]),
        {"price": "0.90", "price_date": "2026-06-01"},
    )
    assert MeterPrice.objects.filter(meter=meter).count() == 2

    # The page must render the price history (both sections) without errors.
    assert client.get(reverse("core:flat_fees", args=[flat.pk])).status_code == 200

    # Inline edits.
    client.post(
        reverse("core:edit_fee", args=[flat.pk, fee.pk]),
        {"title": "Internet + TV", "is_individual": "on"},
    )
    fee.refresh_from_db()
    assert fee.title == "Internet + TV"
    assert fee.is_individual is True
    client.post(
        reverse("core:flat_meter_edit", args=[flat.pk, meter.pk]),
        {"name": "Prąd A", "unit": "kWh"},
    )
    meter.refresh_from_db()
    assert meter.name == "Prąd A"

    # Delete a price of each, then the fee and the meter.
    p = AdminFeePrice.objects.filter(admin_fee=fee).first()
    client.post(reverse("core:delete_fee_price", args=[flat.pk, p.pk]))
    assert not AdminFeePrice.objects.filter(pk=p.pk).exists()
    mp = MeterPrice.objects.filter(meter=meter).first()
    client.post(reverse("core:delete_meter_price", args=[flat.pk, mp.pk]))
    assert not MeterPrice.objects.filter(pk=mp.pk).exists()
    client.post(reverse("core:delete_fee", args=[flat.pk, fee.pk]))
    assert not AdminFee.objects.filter(pk=fee.pk).exists()
    client.post(reverse("core:flat_meter_delete", args=[flat.pk, meter.pk]))
    assert not MeterDefinition.objects.filter(pk=meter.pk).exists()


@pytest.mark.django_db
def test_flat_fees_owner_scoped(setup: tuple) -> None:
    _, _, flat, _ = setup
    other = get_user_model().objects.create_user(username="other@e.com", password="pw")
    other_client = Client()
    other_client.force_login(other)
    # Cannot view or edit fees on someone else's flat.
    assert (
        other_client.get(reverse("core:flat_fees", args=[flat.pk])).status_code == 404
    )
    assert (
        other_client.post(
            reverse("core:edit_fee", args=[flat.pk, 1])
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_fee_and_edit_forms_render(setup: tuple) -> None:
    _, client, flat, room = setup
    assert client.get(reverse("core:fee_add")).status_code == 200
    assert client.get(reverse("core:add_flat")).status_code == 200
    assert client.get(reverse("core:edit_flat", args=[flat.pk])).status_code == 200
    assert client.get(reverse("core:add_room")).status_code == 200
    assert client.get(reverse("core:edit_room", args=[room.pk])).status_code == 200


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
