"""Tests for the dashboard 'Pozostałe opłaty' (unconfirmed fee settlement) alert."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core.models import (
    Contract,
    FeeCalculation,
    FeeCalculationItem,
    FeeCalculationTenant,
    Flat,
    LedgerEntry,
    Room,
)
from apps.core.services.stats import unconfirmed_fees


@pytest.fixture
def fee_setup(db: None) -> tuple:
    user = get_user_model().objects.create_user(
        username="o@example.com", password="pw"
    )
    flat = Flat.objects.create(owner=user, city="C", street="S", code="X")
    room = Room.objects.create(owner=user, flat=flat, room_no=1, beds=1)
    contract = Contract.objects.create(
        owner=user, flat=flat, room=room, contract_number="C/1",
        tenant_name="T", email="t@x.pl",
    )
    # Settlement for June 2026 (confirmed in July 2026, billed in arrears).
    calc = FeeCalculation.objects.create(
        owner=user, flat=flat,
        stamp=timezone.make_aware(datetime(2026, 7, 1)),
        period_start=timezone.make_aware(datetime(2026, 6, 1)),
        period_end=timezone.make_aware(datetime(2026, 6, 30)),
    )
    tenant = FeeCalculationTenant.objects.create(
        owner=user, flat=flat, calculation=calc,
        tenant_name="T", contract_number="C/1", email="t@x.pl",
    )
    FeeCalculationItem.objects.create(
        owner=user, flat=flat, tenant=tenant,
        fee_type="Admin", name="Internet", value=Decimal("60.00"),
    )
    return user, flat, contract, tenant


@pytest.mark.django_db
def test_unconfirmed_fees_detected(fee_setup: tuple) -> None:
    user, _flat, _contract, _tenant = fee_setup
    items = unconfirmed_fees(user)
    assert len(items) == 1
    assert items[0].amount == Decimal("60.00")
    assert items[0].period_label == "06/2026"  # the settlement period
    assert (items[0].bill_year, items[0].bill_month) == (2026, 7)  # confirm month
    assert items[0].overdue is True  # billing month (07/2026) already passed


@pytest.mark.django_db
def test_unconfirmed_fees_excluded_when_confirmed(fee_setup: tuple) -> None:
    user, flat, contract, tenant = fee_setup
    # A fee ledger entry linked to the settlement tenant confirms it.
    LedgerEntry.objects.create(
        owner=user, flat=flat, contract=contract, settlement_tenant=tenant,
        kind=LedgerEntry.Kind.FEE, billing_period=date(2026, 7, 1),
        record_date=timezone.make_aware(datetime(2026, 7, 10)),
        amount_in_taxable=Decimal("0"),
    )
    assert unconfirmed_fees(user) == []
