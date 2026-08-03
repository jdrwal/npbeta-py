"""Tests for contribution funds: balance service, views and owner scoping."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import (
    Contract,
    FeeCalculationItem,
    Flat,
    Fund,
    FundContribution,
    FundExpense,
    LedgerEntry,
    Room,
)
from apps.core.services.fees import save_settlement
from apps.core.services.funds import confirmed_payment_count, fund_balance


@pytest.fixture
def owner_client(db: None) -> tuple:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    client = Client()
    client.force_login(user)
    return user, client


def _flat(user) -> Flat:
    return Flat.objects.create(
        owner=user, city="Wroclaw", street="Testowa", building_no="1", code="ABC"
    )


def _months_ago(months: int) -> date:
    today = timezone.localdate()
    m = today.month - months
    y = today.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _fee(user, flat, billing_period: date) -> LedgerEntry:
    """A confirmed bills payment (feeds the fund)."""
    return LedgerEntry.objects.create(
        owner=user,
        flat=flat,
        kind=LedgerEntry.Kind.FEE,
        billing_period=billing_period,
    )


@pytest.mark.django_db
def test_accrual_counts_confirmed_bill_payments(owner_client: tuple) -> None:
    user, _ = owner_client
    flat = _flat(user)
    fund = Fund.objects.create(
        owner=user,
        flat=flat,
        name="Sprzątanie",
        monthly_amount=Decimal("25.00"),
        start_date=_months_ago(2),
    )
    # Three confirmed bill payments within the window -> 3 × 25.
    _fee(user, flat, _months_ago(2))
    _fee(user, flat, _months_ago(1))
    _fee(user, flat, _months_ago(0))
    # A rent payment must NOT feed the fund.
    LedgerEntry.objects.create(
        owner=user, flat=flat, kind=LedgerEntry.Kind.RENT,
        billing_period=_months_ago(0),
    )
    # A fee payment before the fund started must NOT count.
    _fee(user, flat, _months_ago(5))

    assert confirmed_payment_count(fund, timezone.localdate()) == 3
    bal = fund_balance(fund)
    assert bal.count == 3
    assert bal.accrued == Decimal("75.00")
    assert bal.balance == Decimal("75.00")


@pytest.mark.django_db
def test_balance_is_contributions_minus_expenses(owner_client: tuple) -> None:
    user, _ = owner_client
    flat = _flat(user)
    fund = Fund.objects.create(
        owner=user,
        flat=flat,
        name="Sprzątanie",
        monthly_amount=Decimal("25.00"),
        start_date=_months_ago(3),
    )
    for i in range(4):  # 4 confirmed payments = 100
        _fee(user, flat, _months_ago(i))
    FundContribution.objects.create(
        owner=user, flat=flat, fund=fund, contributed_on=timezone.localdate(),
        amount=Decimal("40.00"),
    )
    FundExpense.objects.create(
        owner=user, flat=flat, fund=fund, spent_on=timezone.localdate(),
        amount=Decimal("30.00"), description="Środki czystości",
    )
    bal = fund_balance(fund)
    assert bal.accrued == Decimal("100.00")
    assert bal.manual == Decimal("40.00")
    assert bal.spent == Decimal("30.00")
    assert bal.balance == Decimal("110.00")


@pytest.mark.django_db
def test_end_date_limits_counted_payments(owner_client: tuple) -> None:
    user, _ = owner_client
    flat = _flat(user)
    fund = Fund.objects.create(
        owner=user,
        flat=flat,
        name="Remontowy",
        monthly_amount=Decimal("10.00"),
        start_date=_months_ago(5),
        end_date=_months_ago(3),  # window covers months -5, -4, -3
    )
    _fee(user, flat, _months_ago(5))
    _fee(user, flat, _months_ago(4))
    _fee(user, flat, _months_ago(3))
    _fee(user, flat, _months_ago(2))  # after end_date -> excluded
    assert confirmed_payment_count(fund, timezone.localdate()) == 3
    assert fund_balance(fund).balance == Decimal("30.00")


@pytest.mark.django_db
def test_other_flat_payments_do_not_count(owner_client: tuple) -> None:
    user, _ = owner_client
    flat = _flat(user)
    other_flat = Flat.objects.create(
        owner=user, city="Krakow", street="Inna", building_no="2", code="DEF"
    )
    fund = Fund.objects.create(
        owner=user, flat=flat, name="Sprzątanie",
        monthly_amount=Decimal("25.00"), start_date=_months_ago(2),
    )
    _fee(user, other_flat, _months_ago(0))  # different flat -> excluded
    assert confirmed_payment_count(fund, timezone.localdate()) == 0
    assert fund_balance(fund).balance == Decimal("0.00")


@pytest.mark.django_db
def test_fund_appears_as_settlement_line(owner_client: tuple) -> None:
    user, _ = owner_client
    flat = _flat(user)
    room = Room.objects.create(owner=user, flat=flat, room_no=1, beds=1)
    Contract.objects.create(
        owner=user, flat=flat, room=room, contract_number="C1",
        tenant_name="Jan", price=Decimal("1000"),
        contract_start=date(2026, 2, 1), contract_end=None,
    )
    Fund.objects.create(
        owner=user, flat=flat, name="Sprzątanie",
        monthly_amount=Decimal("25.00"), start_date=date(2026, 1, 1),
    )
    save_settlement(flat, date(2026, 3, 1), date(2026, 3, 31))
    fund_items = FeeCalculationItem.objects.filter(flat=flat, fee_type="Fund")
    assert fund_items.count() == 1
    item = fund_items.get()
    assert item.name == "Sprzątanie"
    assert item.value == Decimal("25.00")


def _settlement_with_fund(user) -> tuple:
    flat = _flat(user)
    room = Room.objects.create(owner=user, flat=flat, room_no=1, beds=1)
    Contract.objects.create(
        owner=user, flat=flat, room=room, contract_number="C1",
        tenant_name="Jan", price=Decimal("1000"),
        contract_start=date(2026, 2, 1), contract_end=None,
    )
    fund = Fund.objects.create(
        owner=user, flat=flat, name="Sprzątanie",
        monthly_amount=Decimal("25.00"), start_date=date(2026, 1, 1),
    )
    calc = save_settlement(flat, date(2026, 3, 1), date(2026, 3, 31))
    return flat, fund, calc.tenants.get()


@pytest.mark.django_db
def test_confirm_fee_links_entry_and_feeds_fund(owner_client: tuple) -> None:
    user, client = owner_client
    flat, fund, tenant = _settlement_with_fund(user)
    resp = client.post(
        reverse("core:confirm_fee", args=[tenant.pk]),
        {"record_date": "2026-04-05", "billing_period": "2026-04"},
    )
    assert resp.status_code == 302
    entry = LedgerEntry.objects.get(settlement_tenant=tenant)
    assert entry.kind == LedgerEntry.Kind.FEE
    assert entry.contract is not None
    assert entry.contract.contract_number == "C1"
    assert entry.amount_in_taxable == Decimal("25.00")
    assert entry.billing_period == date(2026, 4, 1)
    # The confirmed payment credits the fund (25 × 1 payment).
    assert fund_balance(fund).accrued == Decimal("25.00")


@pytest.mark.django_db
def test_confirm_fee_is_idempotent(owner_client: tuple) -> None:
    user, client = owner_client
    _, _, tenant = _settlement_with_fund(user)
    url = reverse("core:confirm_fee", args=[tenant.pk])
    client.post(url, {"record_date": "2026-04-05", "billing_period": "2026-04"})
    client.post(url, {"record_date": "2026-04-06", "billing_period": "2026-04"})
    assert LedgerEntry.objects.filter(settlement_tenant=tenant).count() == 1


@pytest.mark.django_db
def test_confirm_fee_owner_scoped_404(owner_client: tuple) -> None:
    user, client = owner_client
    other = get_user_model().objects.create_user(
        username="other2@example.com", password="pw"
    )
    _, _, tenant = _settlement_with_fund(other)
    resp = client.get(reverse("core:confirm_fee", args=[tenant.pk]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_funds_page_renders(owner_client: tuple) -> None:
    user, client = owner_client
    flat = _flat(user)
    Fund.objects.create(
        owner=user, flat=flat, name="Sprzątanie",
        monthly_amount=Decimal("25.00"), start_date=_months_ago(1),
    )
    response = client.get(reverse("core:funds"))
    assert response.status_code == 200
    assert b"Sprz" in response.content


@pytest.mark.django_db
def test_funds_page_requires_login(db: None) -> None:
    response = Client().get(reverse("core:funds"))
    assert response.status_code == 302


@pytest.mark.django_db
def test_add_fund_creates(owner_client: tuple) -> None:
    user, client = owner_client
    flat = _flat(user)
    response = client.post(
        reverse("core:fund_add"),
        {
            "flat": flat.pk,
            "name": "Sprzątanie",
            "monthly_amount": "25.00",
            "start_date": "2026-01-01",
        },
    )
    assert response.status_code == 302
    assert Fund.objects.filter(owner=user, name="Sprzątanie").exists()


@pytest.mark.django_db
def test_add_contribution_and_expense(owner_client: tuple) -> None:
    user, client = owner_client
    flat = _flat(user)
    fund = Fund.objects.create(
        owner=user, flat=flat, name="Sprzątanie",
        monthly_amount=Decimal("25.00"), start_date=_months_ago(0),
    )
    client.post(
        reverse("core:fund_add_contribution", args=[fund.pk]),
        {"contributed_on": "2026-02-01", "amount": "50.00", "note": "dopłata"},
    )
    client.post(
        reverse("core:fund_add_expense", args=[fund.pk]),
        {"spent_on": "2026-02-10", "amount": "20.00", "description": "mydło"},
    )
    assert FundContribution.objects.filter(fund=fund, amount=Decimal("50.00")).exists()
    assert FundExpense.objects.filter(fund=fund, amount=Decimal("20.00")).exists()


@pytest.mark.django_db
def test_fund_edit_and_delete(owner_client: tuple) -> None:
    user, client = owner_client
    flat = _flat(user)
    fund = Fund.objects.create(
        owner=user, flat=flat, name="Sprzątanie",
        monthly_amount=Decimal("25.00"), start_date=_months_ago(0),
    )
    client.post(
        reverse("core:fund_edit", args=[fund.pk]),
        {"name": "Sprzątanie klatki", "monthly_amount": "30.00",
         "start_date": fund.start_date.isoformat()},
    )
    fund.refresh_from_db()
    assert fund.name == "Sprzątanie klatki"
    assert fund.monthly_amount == Decimal("30.00")

    client.post(reverse("core:fund_delete", args=[fund.pk]))
    assert not Fund.objects.filter(pk=fund.pk).exists()


@pytest.mark.django_db
def test_fund_owner_scoping_404(owner_client: tuple) -> None:
    user, client = owner_client
    other = get_user_model().objects.create_user(
        username="other@example.com", password="pw"
    )
    other_flat = Flat.objects.create(
        owner=other, city="Krakow", street="Inna", building_no="9", code="XYZ"
    )
    other_fund = Fund.objects.create(
        owner=other, flat=other_flat, name="Obcy",
        monthly_amount=Decimal("25.00"), start_date=timezone.localdate(),
    )
    response = client.post(reverse("core:fund_delete", args=[other_fund.pk]))
    assert response.status_code == 404
    assert Fund.objects.filter(pk=other_fund.pk).exists()


@pytest.mark.django_db
def test_end_date_before_start_rejected(owner_client: tuple) -> None:
    user, client = owner_client
    flat = _flat(user)
    start = timezone.localdate()
    end = start - timedelta(days=40)
    response = client.post(
        reverse("core:fund_add"),
        {
            "flat": flat.pk,
            "name": "Zły",
            "monthly_amount": "25.00",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert response.status_code == 200  # re-rendered with errors
    assert not Fund.objects.filter(name="Zły").exists()
