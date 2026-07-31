"""Tests for meter/reading CRUD and records pagination."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import (
    Flat,
    LedgerEntry,
    MeterDefinition,
    MeterPrice,
    MeterReading,
)
from apps.core.services.fees import _counter_price


@pytest.fixture
def meter_setup(db: None) -> tuple:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    client = Client()
    client.force_login(user)
    flat = Flat.objects.create(owner=user, city="C", street="S", code="X")
    meter = MeterDefinition.objects.create(owner=user, flat=flat, name="Prad", unit="kWh")
    reading = MeterReading.objects.create(
        owner=user, flat=flat, meter=meter, read_date=date(2026, 1, 1), value=Decimal("100")
    )
    return user, client, flat, meter, reading


@pytest.mark.django_db
def test_counters_page_renders(meter_setup: tuple) -> None:
    _, client, _, _, _ = meter_setup
    assert client.get(reverse("core:counters")).status_code == 200


@pytest.mark.django_db
def test_meter_create(meter_setup: tuple) -> None:
    user, client, flat, _, _ = meter_setup
    response = client.post(
        reverse("core:add_meter"), {"flat": flat.pk, "name": "Gaz", "unit": "m³"}
    )
    assert response.status_code == 302
    assert MeterDefinition.objects.filter(owner=user, name="Gaz").exists()


@pytest.mark.django_db
def test_meter_readings_page_renders(meter_setup: tuple) -> None:
    _, client, _, meter, _ = meter_setup
    assert client.get(reverse("core:meter_readings", args=[meter.pk])).status_code == 200


@pytest.mark.django_db
def test_reading_delete(meter_setup: tuple) -> None:
    _, client, _, _, reading = meter_setup
    response = client.post(reverse("core:delete_reading", args=[reading.pk]))
    assert response.status_code == 302
    assert not MeterReading.objects.filter(pk=reading.pk).exists()


@pytest.mark.django_db
def test_records_monthly_summary(meter_setup: tuple) -> None:
    user, client, flat, _, _ = meter_setup
    for i in range(5):
        LedgerEntry.objects.create(
            owner=user,
            flat=flat,
            short_desc=f"Rec {i}",
            amount_in_taxable=Decimal("1000"),
            record_date=timezone.make_aware(datetime(2026, 1, 10)),
            billing_period=date(2026, 1, 1),
        )
    response = client.get(reverse("core:records"), {"year": 2026, "month": 1})
    assert response.status_code == 200
    assert len(response.context["rent_entries"]) == 5
    assert response.context["totals"]["income"] == Decimal("5000")
    # No entries in a different month.
    other = client.get(reverse("core:records"), {"year": 2026, "month": 2})
    assert len(other.context["rent_entries"]) == 0


@pytest.mark.django_db
def test_counter_price_is_per_meter(meter_setup: tuple) -> None:
    """Each meter uses its OWN latest price by date — no shared 'oldest date'."""
    user, _, flat, meter, _ = meter_setup
    m2 = MeterDefinition.objects.create(owner=user, flat=flat, name="Gaz", unit="m³")
    MeterPrice.objects.create(
        owner=user, flat=flat, meter=meter, price=Decimal("0.80"), price_date=date(2026, 1, 1)
    )
    MeterPrice.objects.create(
        owner=user, flat=flat, meter=meter, price=Decimal("0.90"), price_date=date(2026, 6, 1)
    )
    MeterPrice.objects.create(
        owner=user, flat=flat, meter=m2, price=Decimal("3.00"), price_date=date(2026, 3, 1)
    )
    # Each meter picks its own most recent price effective before the day.
    assert _counter_price(meter, date(2026, 7, 1)) == Decimal("0.90")
    assert _counter_price(m2, date(2026, 7, 1)) == Decimal("3.00")
    # An earlier day sees the earlier price for meter 1, and none yet for meter 2.
    assert _counter_price(meter, date(2026, 3, 1)) == Decimal("0.80")
    assert _counter_price(m2, date(2026, 2, 1)) is None
