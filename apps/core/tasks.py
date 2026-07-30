"""Celery tasks for background work (settlements and tenant emails)."""

from __future__ import annotations

from datetime import date

from celery import shared_task


@shared_task
def run_settlement_task(flat_id: int, period_start: str, period_end: str) -> int:
    """Compute and persist a settlement in the background. Returns its id."""
    from apps.core.models import Flat
    from apps.core.services.fees import save_settlement

    flat = Flat.objects.get(pk=flat_id)
    calc = save_settlement(
        flat, date.fromisoformat(period_start), date.fromisoformat(period_end)
    )
    return calc.pk


@shared_task
def email_settlement_task(calc_id: int) -> int:
    """Email a saved settlement to its tenants. Returns the number sent."""
    from apps.core.models import FeeCalculation
    from apps.core.services.notifications import send_settlement_emails

    calc = FeeCalculation.objects.get(pk=calc_id)
    return send_settlement_emails(calc)
