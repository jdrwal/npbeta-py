"""Tests for Celery tasks and settlement email notifications."""

from datetime import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
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
    assert mail.outbox[0].bcc == ["owner@example.com"]
    assert "Prad" in mail.outbox[0].body
    assert "Total: 50.00 PLN" in mail.outbox[0].body


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
