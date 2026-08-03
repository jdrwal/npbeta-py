"""Tests for Celery tasks and settlement email notifications."""

from datetime import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.core.models import (
    FeeCalculation,
    FeeCalculationItem,
    FeeCalculationTenant,
    Flat,
)
from apps.core.services.notifications import send_settlement_emails
from apps.core.tasks import email_settlement_task, run_settlement_task


@pytest.fixture
def calc_with_tenant(db: None) -> FeeCalculation:
    user = get_user_model().objects.create_user(
        username="owner@example.com", password="pw"
    )
    flat = Flat.objects.create(owner=user, city="C", street="S", code="X")
    calc = FeeCalculation.objects.create(
        owner=user,
        flat=flat,
        stamp=timezone.make_aware(datetime(2026, 2, 1)),
        period_start=timezone.make_aware(datetime(2026, 1, 1)),
        period_end=timezone.make_aware(datetime(2026, 1, 31)),
    )
    tenant = FeeCalculationTenant.objects.create(
        owner=user,
        flat=flat,
        calculation=calc,
        tenant_name="Tenant",
        email="tenant@example.com",
    )
    FeeCalculationItem.objects.create(
        owner=user,
        flat=flat,
        tenant=tenant,
        fee_type="Counter",
        name="Prad",
        value=Decimal("50.00"),
    )
    return calc


@pytest.mark.django_db
def test_send_settlement_emails(calc_with_tenant: FeeCalculation) -> None:
    sent = send_settlement_emails(calc_with_tenant)
    assert sent == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["tenant@example.com"]
    assert mail.outbox[0].cc == ["owner@example.com"]
    assert "Prad" in mail.outbox[0].body
    assert "50.00 zł" in mail.outbox[0].body
    assert "SUMA" in mail.outbox[0].body
    assert "rozlicz-najem.pl" in mail.outbox[0].body  # marketing footer


@pytest.mark.django_db
def test_settlement_email_uses_owner_template(
    calc_with_tenant: FeeCalculation,
) -> None:
    from apps.core.models import EmailTemplate

    EmailTemplate.objects.create(
        owner=calc_with_tenant.owner,
        kind=EmailTemplate.Kind.SETTLEMENT,
        name="Custom",
        subject="Rozliczenie {flat}",
        body="Cześć {tenant_name},\n{items}\nRazem: {total}\nMój podpis",
        is_active=True,
    )
    send_settlement_emails(calc_with_tenant)
    body = mail.outbox[0].body
    assert "Cześć Tenant," in body  # template greeting used
    assert "Mój podpis" in body  # template signature used
    assert "Prad: 50.00 zł" in body  # {items} injected
    assert "Razem: 50.00 zł" in body  # {total} injected


@pytest.mark.django_db
def test_settlement_email_preview_endpoint(
    client: Client, calc_with_tenant: FeeCalculation
) -> None:
    from django.urls import reverse

    calc = calc_with_tenant
    tenant = calc.tenants.get()
    client.force_login(calc.owner)
    url = reverse("core:settlement_email_preview", args=[calc.pk, tenant.pk])
    resp = client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert data["subject"]
    assert "Prad" in data["body"]
    assert "50.00 zł" in data["body"]
    assert "rozlicz-najem.pl" in data["body"]  # footer shown in preview
    assert len(mail.outbox) == 0  # preview must not send


@pytest.mark.django_db
def test_settlement_email_preview_owner_scoped(
    client: Client, calc_with_tenant: FeeCalculation
) -> None:
    from django.urls import reverse

    calc = calc_with_tenant
    tenant = calc.tenants.get()
    other = get_user_model().objects.create_user(
        username="intruder@example.com", password="pw"
    )
    client.force_login(other)
    url = reverse("core:settlement_email_preview", args=[calc.pk, tenant.pk])
    resp = client.get(url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_settlement_email_send_one_endpoint(
    client: Client, calc_with_tenant: FeeCalculation
) -> None:
    from django.urls import reverse

    calc = calc_with_tenant
    tenant = calc.tenants.get()
    client.force_login(calc.owner)
    url = reverse("core:settlement_email_send_one", args=[calc.pk, tenant.pk])
    resp = client.post(url)
    assert resp.status_code == 200
    assert resp.json()["sent"] is True
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["tenant@example.com"]


@pytest.mark.django_db
def test_settlement_email_send_one_no_address(
    client: Client, calc_with_tenant: FeeCalculation
) -> None:
    from django.urls import reverse

    from apps.core.models import FeeCalculationTenant

    calc = calc_with_tenant
    no_addr = FeeCalculationTenant.objects.create(
        owner=calc.owner, flat=calc.flat, calculation=calc,
        tenant_name="No Mail", email="",
    )
    client.force_login(calc.owner)
    url = reverse("core:settlement_email_send_one", args=[calc.pk, no_addr.pk])
    resp = client.post(url)
    assert resp.status_code == 400
    assert resp.json()["sent"] is False
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_email_settlement_task_runs(calc_with_tenant: FeeCalculation) -> None:
    result = email_settlement_task.delay(calc_with_tenant.pk)
    assert result.get() == 1
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_run_settlement_task_creates_calculation() -> None:
    call_command("loaddata", "tests/fixtures/golden_fees.json", verbosity=0)
    source = FeeCalculation.objects.get()
    before = FeeCalculation.objects.count()

    calc_id = run_settlement_task.delay(
        source.flat_id,
        source.period_start.date().isoformat(),
        source.period_end.date().isoformat(),
    ).get()

    assert FeeCalculation.objects.count() == before + 1
    assert FeeCalculation.objects.filter(pk=calc_id).exists()
