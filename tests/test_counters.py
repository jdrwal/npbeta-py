"""Tests for meter/reading CRUD and records pagination."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Flat, LedgerEntry, MeterDefinition, MeterReading


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
def test_records_pagination(meter_setup: tuple) -> None:
    user, client, flat, _, _ = meter_setup
    for i in range(60):
        LedgerEntry.objects.create(
            owner=user,
            flat=flat,
            short_desc=f"Rec {i}",
            record_date=timezone.make_aware(datetime(2026, 1, 1)),
        )
    response = client.get(reverse("core:records"), {"page": 2})
    assert response.status_code == 200
    assert len(response.context["page"]) == 10  # 60 total, 50 per page
