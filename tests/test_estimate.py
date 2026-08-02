"""Tests for meter-reading estimation (service, endpoint, persistence)."""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.core.models import Flat, MeterDefinition, MeterReading
from apps.core.services.estimate import estimate_flat_readings, estimate_reading


@pytest.fixture
def flat_with_history(db: None) -> tuple:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    client = Client()
    client.force_login(user)
    flat = Flat.objects.create(owner=user, city="C", street="S", code="X")
    meter = MeterDefinition.objects.create(
        owner=user, flat=flat, name="Prąd", unit="kWh"
    )
    # 30 kWh used over 31 days -> ~0.9677 kWh/day.
    MeterReading.objects.create(
        owner=user, flat=flat, meter=meter,
        read_date=date(2026, 1, 1), value=Decimal("100"),
    )
    MeterReading.objects.create(
        owner=user, flat=flat, meter=meter,
        read_date=date(2026, 2, 1), value=Decimal("130"),
    )
    return user, client, flat, meter


@pytest.mark.django_db
def test_estimate_reading_projects_forward(flat_with_history: tuple) -> None:
    _, _, _, meter = flat_with_history
    # 28 days past the last reading at 30/31 kWh/day: 130 + 30/31*28 = 157.097.
    assert estimate_reading(meter, date(2026, 3, 1), 12) == Decimal("157.097")


@pytest.mark.django_db
def test_estimate_needs_two_readings(flat_with_history: tuple) -> None:
    user, _, flat, _ = flat_with_history
    lonely = MeterDefinition.objects.create(
        owner=user, flat=flat, name="Gaz", unit="m³"
    )
    MeterReading.objects.create(
        owner=user, flat=flat, meter=lonely,
        read_date=date(2026, 1, 1), value=Decimal("5"),
    )
    assert estimate_reading(lonely, date(2026, 3, 1), 12) is None


@pytest.mark.django_db
def test_estimate_flat_readings_skips_meters_without_history(
    flat_with_history: tuple,
) -> None:
    user, _, flat, meter = flat_with_history
    MeterDefinition.objects.create(owner=user, flat=flat, name="Gaz", unit="m³")
    estimates = estimate_flat_readings(flat, date(2026, 3, 1), 12)
    assert list(estimates.keys()) == [meter.id]


@pytest.mark.django_db
def test_estimate_endpoint_returns_json(flat_with_history: tuple) -> None:
    _, client, flat, meter = flat_with_history
    response = client.get(
        reverse("core:estimate_readings"),
        {"flat": flat.pk, "months": 12, "read_date": "2026-03-01"},
    )
    assert response.status_code == 200
    assert response.json()["estimates"][str(meter.id)] == "157.097"


@pytest.mark.django_db
def test_estimate_endpoint_owner_scoped(flat_with_history: tuple) -> None:
    _, _, flat, _ = flat_with_history
    other = get_user_model().objects.create_user(username="other@x.com", password="pw")
    other_client = Client()
    other_client.force_login(other)
    response = other_client.get(
        reverse("core:estimate_readings"), {"flat": flat.pk, "read_date": "2026-03-01"}
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_add_reading_page_shows_estimate_controls(flat_with_history: tuple) -> None:
    _, client, _, _ = flat_with_history
    response = client.get(reverse("core:add_reading"))
    assert response.status_code == 200
    assert b"estimate-btn" in response.content
    assert "Oszacuj".encode() in response.content


@pytest.mark.django_db
def test_add_reading_persists_estimated_flag(flat_with_history: tuple) -> None:
    user, client, flat, meter = flat_with_history
    response = client.post(
        reverse("core:add_reading"),
        {
            "flat": flat.pk,
            "read_date": "2026-03-01",
            f"value_{meter.pk}": "157.097",
            f"estimated_{meter.pk}": "1",
        },
    )
    assert response.status_code == 302
    reading = MeterReading.objects.get(meter=meter, read_date=date(2026, 3, 1))
    assert reading.is_estimated is True


@pytest.mark.django_db
def test_add_reading_real_by_default(flat_with_history: tuple) -> None:
    user, client, flat, meter = flat_with_history
    client.post(
        reverse("core:add_reading"),
        {
            "flat": flat.pk,
            "read_date": "2026-03-05",
            f"value_{meter.pk}": "160",
            f"estimated_{meter.pk}": "0",
        },
    )
    reading = MeterReading.objects.get(meter=meter, read_date=date(2026, 3, 5))
    assert reading.is_estimated is False
