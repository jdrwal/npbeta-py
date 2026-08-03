"""Email notifications for settlements (the legacy sendMail was a stub)."""

from __future__ import annotations

from decimal import Decimal

from django.core.mail.backends.base import BaseEmailBackend

from apps.core.models import FeeCalculation, FeeCalculationTenant
from apps.core.services.mailer import (
    owner_address,
    owner_connection,
    render_text,
    send_owner_email,
)


def _period_label(calc: FeeCalculation) -> str:
    return f"{calc.period_start:%Y-%m-%d} – {calc.period_end:%Y-%m-%d}"


def _items_block(tenant: FeeCalculationTenant) -> tuple[str, Decimal]:
    """Rendered per-item breakdown text and its total for one tenant."""
    lines: list[str] = []
    total = Decimal(0)
    for item in tenant.items.all():
        lines.append(f"  {item.name}: {item.value:.2f} zł")
        total += item.value
    return "\n".join(lines), total


def _fallback_body(tenant: FeeCalculationTenant) -> str:
    """Built-in body, used only when the template body renders empty."""
    calc = tenant.calculation
    items, total = _items_block(tenant)
    return (
        f"Rozliczenie opłat — {calc.flat}\n"
        f"Okres: {_period_label(calc)}\n\n"
        f"{items}\n\n"
        f"SUMA: {total:.2f} zł"
    )


def render_settlement_email(
    calc: FeeCalculation, tenant: FeeCalculationTenant
) -> tuple[str, str]:
    """Rendered (subject, body) of the settlement email for one tenant.

    The body does NOT include the shared marketing footer — that is appended by
    the central send path (and by the preview view) so it appears exactly once.
    Used by both the actual sending and the on-screen preview so they match.
    """
    from apps.core.models import EmailTemplate
    from apps.core.services.mailer import get_template

    subject_tpl, body_tpl = get_template(calc.owner, EmailTemplate.Kind.SETTLEMENT)
    owner = calc.owner
    owner_name = owner.get_full_name() or owner.get_username()
    items, total = _items_block(tenant)
    context = {
        "tenant_name": tenant.tenant_name or "",
        "contract_number": tenant.contract_number or "",
        "flat": str(calc.flat),
        "period": _period_label(calc),
        "items": items,
        "total": f"{total:.2f} zł",
        "owner_name": owner_name,
    }
    subject = render_text(
        subject_tpl or "Rozliczenie mediów — {flat} ({period})", context
    )
    body = render_text(body_tpl or "", context).strip()
    if not body:
        body = _fallback_body(tenant)
    return subject, body


def send_settlement_email_to(
    calc: FeeCalculation,
    tenant: FeeCalculationTenant,
    *,
    connection: BaseEmailBackend | None = None,
    cc: list[str] | None = None,
) -> bool:
    """Send the settlement email to a single tenant. Returns False if no address.

    ``connection`` and ``cc`` may be supplied to reuse them across a batch.
    """
    if not tenant.email:
        return False
    if cc is None:
        owner_cc = owner_address(calc.owner)
        cc = [owner_cc] if owner_cc else []
    subject, body = render_settlement_email(calc, tenant)
    send_owner_email(
        calc.owner,
        subject=subject,
        body=body,
        to=[tenant.email],
        cc=cc,
        flat=calc.flat,
        connection=connection,
    )
    return True


def send_settlement_emails(calc: FeeCalculation) -> int:
    """Email each tenant their settlement breakdown (owner in CC). Returns count.

    Both the subject and the body come from the owner's SETTLEMENT template
    (falling back to the built-in default). The per-tenant fee breakdown and
    total are injected via the ``{items}`` and ``{total}`` placeholders.
    """
    owner = calc.owner
    # Reuse one SMTP connection and the same owner CC across the batch.
    connection, _ = owner_connection(owner)
    owner_cc = owner_address(owner)
    cc = [owner_cc] if owner_cc else []

    sent = 0
    for tenant in calc.tenants.all():
        if send_settlement_email_to(calc, tenant, connection=connection, cc=cc):
            sent += 1
    return sent

