"""Tests for the lump-sum rental tax service (port of getTaxByMonth)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Flat, LedgerEntry, TaxDue
from apps.core.services.tax import monthly_tax


@pytest.fixture
def owner_flat(db: None) -> tuple:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    flat = Flat.objects.create(
        owner=user,
        city="C",
        street="S",
        code="X",
        rental_start=timezone.make_aware(datetime(2024, 1, 1)),
    )
    return user, flat


def _entry(user, flat, when: datetime, taxable: str) -> None:
    LedgerEntry.objects.create(
        owner=user,
        flat=flat,
        record_date=timezone.make_aware(when),
        amount_in_taxable=Decimal(taxable),
    )


@pytest.mark.django_db
def test_monthly_tax_amount_and_rounding(owner_flat: tuple) -> None:
    user, flat = owner_flat
    _entry(user, flat, datetime(2025, 1, 10), "1000")
    _entry(user, flat, datetime(2025, 1, 20), "100")  # 1100 * 0.085 = 93.5 -> 94

    result = monthly_tax(user, 2025, 1)
    assert result.taxable == Decimal("1100")
    assert result.tax == 94
    assert result.deadline == date(2025, 2, 20)


@pytest.mark.django_db
def test_december_deadline_is_end_of_january(owner_flat: tuple) -> None:
    user, _ = owner_flat
    assert monthly_tax(user, 2025, 12).deadline == date(2026, 1, 31)


@pytest.mark.django_db
def test_threshold_switches_to_higher_rate(owner_flat: tuple) -> None:
    user, flat = owner_flat
    # January pushes YTD to 90k (all at 8.5%).
    _entry(user, flat, datetime(2025, 1, 15), "90000")
    # February adds 20k: 10k fills the 8.5% band, 10k taxed at 12.5%.
    _entry(user, flat, datetime(2025, 2, 15), "20000")

    january = monthly_tax(user, 2025, 1)
    february = monthly_tax(user, 2025, 2)

    assert january.tax == 7650  # 90000 * 0.085
    # 10000 * 0.085 + 10000 * 0.125 = 850 + 1250 = 2100
    assert february.tax == 2100


@pytest.mark.django_db
def test_paid_date_from_taxdue(owner_flat: tuple) -> None:
    user, _ = owner_flat
    TaxDue.objects.create(
        owner=user,
        period="03/2025",
        tax_date=timezone.make_aware(datetime(2025, 4, 18)),
        tax_amount=50,
    )
    assert monthly_tax(user, 2025, 3).paid_date == date(2025, 4, 18)


@pytest.mark.django_db
def test_tax_page_renders(owner_flat: tuple) -> None:
    user, _ = owner_flat
    client = Client()
    client.force_login(user)
    assert client.get(reverse("core:tax")).status_code == 200
