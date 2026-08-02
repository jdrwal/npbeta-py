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


@shared_task
def send_flat_broadcast_task(
    owner_id: int,
    flat_id: int,
    subject: str,
    body: str,
    template_id: int | None = None,
) -> int:
    """Send an ad-hoc broadcast to all active tenants of a flat (tenants in BCC).

    Returns the number of tenant addresses reached (0 if none / not sent).
    """
    from apps.accounts.models import User
    from apps.core.models import EmailTemplate, Flat
    from apps.core.services.mailer import send_flat_broadcast

    owner = User.objects.get(pk=owner_id)
    flat = Flat.objects.get(pk=flat_id, owner=owner)
    template = (
        EmailTemplate.objects.filter(pk=template_id, owner=owner).first()
        if template_id
        else None
    )
    log = send_flat_broadcast(owner, flat, subject, body, template=template)
    return len(log.bcc) if log is not None else 0

