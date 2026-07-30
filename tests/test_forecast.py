"""Tests for forecast income and mortgage schedule services."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Contract, Flat, Room
from apps.core.services.forecast import contract_ratio, forecast_income
from apps.core.services.mortgage import mortgage_schedule


@pytest.fixture
def owner_setup(db: None) -> tuple:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    flat = Flat.objects.create(owner=user, city="C", street="S", code="X")
    room = Room.objects.create(owner=user, flat=flat, room_no=1, beds=1)
    return user, flat, room


def _contract(user, flat, room, start: date, end: date, price: str) -> Contract:
    return Contract.objects.create(
        owner=user,
        flat=flat,
        room=room,
        contract_start=start,
        contract_end=end,
        price=Decimal(price),
    )


@pytest.mark.django_db
def test_ratio_full_month(owner_setup: tuple) -> None:
    user, flat, room = owner_setup
    contract = _contract(user, flat, room, date(2025, 4, 1), date(2025, 4, 30), "1000")
    assert contract_ratio(contract, 2025, 4) == Decimal(1)


@pytest.mark.django_db
def test_ratio_half_month(owner_setup: tuple) -> None:
    user, flat, room = owner_setup
    contract = _contract(user, flat, room, date(2025, 4, 1), date(2025, 4, 15), "1000")
    assert contract_ratio(contract, 2025, 4) == Decimal(15) / Decimal(30)


@pytest.mark.django_db
def test_forecast_income_prorated(owner_setup: tuple) -> None:
    user, flat, room = owner_setup
    _contract(user, flat, room, date(2025, 4, 1), date(2025, 4, 15), "1000")
    assert forecast_income(flat, 2025, 4) == Decimal("500.00")


@pytest.mark.django_db
def test_mortgage_schedule(owner_setup: tuple) -> None:
    user, flat, _ = owner_setup
    flat.mortgage_start = timezone.make_aware(datetime(2020, 1, 1))
    flat.mortgage_amount = Decimal("2000")
    flat.mortgage_interest = Decimal("300")
    flat.save()

    schedule = mortgage_schedule(user, months=12)
    assert len(schedule) == 12
    assert schedule[0].amount == Decimal("2000.00")
    assert schedule[0].interest == Decimal("300.00")


@pytest.mark.django_db
def test_no_mortgage_no_schedule(owner_setup: tuple) -> None:
    user, _, _ = owner_setup
    assert mortgage_schedule(user, months=12) == []


@pytest.mark.django_db
def test_forecast_page_renders(owner_setup: tuple) -> None:
    user, _, _ = owner_setup
    client = Client()
    client.force_login(user)
    assert client.get(reverse("core:forecast")).status_code == 200
