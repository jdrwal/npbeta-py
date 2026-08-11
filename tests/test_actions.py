"""Tests for create/action views and the save_settlement service."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.forms import SettlementForm
from apps.core.models import (
    AdminFee,
    AdminFeeInvoice,
    Contract,
    FeeCalculation,
    FeeCalculationItem,
    Flat,
    Room,
)
from apps.core.services.fees import calculate_fees, save_settlement


@pytest.fixture
def owner_client(db: None) -> tuple:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    client = Client()
    client.force_login(user)
    return user, client


@pytest.mark.django_db
def test_add_flat_creates_flat(owner_client: tuple) -> None:
    user, client = owner_client
    response = client.post(
        reverse("core:add_flat"),
        {
            "city": "Wroclaw",
            "street": "Testowa",
            "building_no": "1",
            "flat_no": "2",
            "code": "ABC123",
            "room_count": 3,
        },
    )
    assert response.status_code == 302
    assert Flat.objects.filter(owner=user, street="Testowa").exists()


@pytest.mark.django_db
def test_run_settlement_form_renders(owner_client: tuple) -> None:
    _, client = owner_client
    response = client.get(reverse("core:run_settlement"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_settlement_form_defaults_to_previous_month(owner_client: tuple) -> None:
    user, _ = owner_client
    form = SettlementForm(user=user)
    today = timezone.localdate()
    first_this = today.replace(day=1)
    prev_end = first_this - timedelta(days=1)
    assert form.fields["period_start"].initial == prev_end.replace(day=1)
    assert form.fields["period_end"].initial == prev_end


@pytest.mark.django_db
def test_settlement_rejects_future_period(owner_client: tuple) -> None:
    user, _ = owner_client
    flat = Flat.objects.create(owner=user, city="X", street="Y", code="C")
    today = timezone.localdate()
    future = today + timedelta(days=10)
    form = SettlementForm(
        data={
            "flat": flat.pk,
            "period_start": today.replace(day=1).isoformat(),
            "period_end": future.isoformat(),
        },
        user=user,
    )
    assert not form.is_valid()
    assert any("przyszłości" in e for e in form.non_field_errors())


@pytest.mark.django_db
def test_save_settlement_matches_calculate_fees() -> None:
    call_command("loaddata", "tests/fixtures/golden_fees.json", verbosity=0)
    source = FeeCalculation.objects.get()
    flat = source.flat
    start, end = source.period_start.date(), source.period_end.date()

    calc = save_settlement(flat, start, end)
    lines = calculate_fees(flat, start, end)
    items = FeeCalculationItem.objects.filter(tenant__calculation=calc)

    assert items.count() == len(lines)
    assert sum(i.value for i in items) == sum(line.value for line in lines)


@pytest.mark.django_db
def test_invoice_fee_included_in_settlement(owner_client: tuple) -> None:
    """An invoice-type fee is billed at its recorded monthly amount, split among
    the active tenants (a full month with one tenant = the full amount)."""
    user, _ = owner_client
    flat = Flat.objects.create(
        owner=user, city="Wroclaw", street="Testowa", building_no="1", code="ABC"
    )
    room = Room.objects.create(owner=user, flat=flat, room_no="1", beds=1)
    Contract.objects.create(
        owner=user, flat=flat, room=room, contract_number="C1", tenant_name="Jan",
        contract_start=date(2026, 1, 1), contract_end=date(2026, 12, 31),
    )
    fee = AdminFee.objects.create(
        owner=user, flat=flat, title="Prąd (faktura)", is_invoice=True
    )
    AdminFeeInvoice.objects.create(
        owner=user, flat=flat, admin_fee=fee,
        period=date(2026, 3, 1), amount=Decimal("300.00"),
    )
    lines = calculate_fees(flat, date(2026, 3, 1), date(2026, 3, 31))
    billed = [line for line in lines if line.name == "Prąd (faktura)"]
    assert len(billed) == 1
    assert billed[0].fee_type == "Admin"
    assert billed[0].value == Decimal("300.00")


@pytest.mark.django_db
def test_invoice_fee_split_between_two_tenants(owner_client: tuple) -> None:
    """Two active tenants split the invoiced amount equally over a full month."""
    user, _ = owner_client
    flat = Flat.objects.create(
        owner=user, city="Wroclaw", street="Testowa", building_no="1", code="ABC"
    )
    r1 = Room.objects.create(owner=user, flat=flat, room_no="1", beds=1)
    r2 = Room.objects.create(owner=user, flat=flat, room_no="2", beds=1)
    for i, room in enumerate((r1, r2), start=1):
        Contract.objects.create(
            owner=user, flat=flat, room=room, contract_number=f"C{i}",
            tenant_name=f"Najemca {i}",
            contract_start=date(2026, 1, 1), contract_end=date(2026, 12, 31),
        )
    fee = AdminFee.objects.create(
        owner=user, flat=flat, title="Prąd (faktura)", is_invoice=True
    )
    AdminFeeInvoice.objects.create(
        owner=user, flat=flat, admin_fee=fee,
        period=date(2026, 3, 1), amount=Decimal("300.00"),
    )
    lines = calculate_fees(flat, date(2026, 3, 1), date(2026, 3, 31))
    billed = [line for line in lines if line.name == "Prąd (faktura)"]
    assert len(billed) == 2
    assert all(line.value == Decimal("150.00") for line in billed)


@pytest.mark.django_db
def test_add_invoice_fee_and_amount_views(owner_client: tuple) -> None:
    """The 'invoice' kind creates an invoice fee; its monthly amount upserts."""
    user, client = owner_client
    flat = Flat.objects.create(
        owner=user, city="Wroclaw", street="Testowa", building_no="1", code="ABC"
    )
    client.post(
        reverse("core:fee_add"),
        {"flat": flat.pk, "kind": "invoice", "title": "Prąd (faktura)"},
    )
    fee = AdminFee.objects.get(flat=flat, title="Prąd (faktura)")
    assert fee.is_invoice

    url = reverse("core:add_invoice_amount", args=[flat.pk, fee.pk])
    client.post(url, {"period": "2026-03", "amount": "300.00"})
    inv = AdminFeeInvoice.objects.get(admin_fee=fee)
    assert inv.period == date(2026, 3, 1)
    assert inv.amount == Decimal("300.00")

    # Re-posting the same month updates in place (no duplicate).
    client.post(url, {"period": "2026-03", "amount": "320.00"})
    assert AdminFeeInvoice.objects.filter(admin_fee=fee).count() == 1
    inv.refresh_from_db()
    assert inv.amount == Decimal("320.00")


@pytest.mark.django_db
def test_delete_settlement(owner_client: tuple) -> None:
    user, client = owner_client
    flat = Flat.objects.create(owner=user, city="X", street="Y", code="C")
    calc = FeeCalculation.objects.create(
        owner=user,
        flat=flat,
        stamp="2026-01-01T00:00:00Z",
        period_start="2026-01-01T00:00:00Z",
        period_end="2026-01-31T00:00:00Z",
    )
    response = client.post(reverse("core:delete_settlement", args=[calc.pk]))
    assert response.status_code == 302
    assert not FeeCalculation.objects.filter(pk=calc.pk).exists()
