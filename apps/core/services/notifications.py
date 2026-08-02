"""Email notifications for settlements (the legacy sendMail was a stub)."""

from __future__ import annotations

from decimal import Decimal

from apps.core.models import FeeCalculation, FeeCalculationTenant
from apps.core.services.mailer import (
    owner_address,
    owner_connection,
    render_text,
    send_owner_email,
)


def _period_label(calc: FeeCalculation) -> str:
    return f"{calc.period_start:%Y-%m-%d} – {calc.period_end:%Y-%m-%d}"


def _body(tenant: FeeCalculationTenant) -> str:
    calc = tenant.calculation
    lines = [
        f"Utility settlement for {calc.flat}",
        f"Period: {calc.period_start:%Y-%m-%d} – {calc.period_end:%Y-%m-%d}",
        "",
    ]
    total = Decimal(0)
    for item in tenant.items.all():
        lines.append(f"  {item.fee_type} {item.name}: {item.value} PLN")
        total += item.value
    lines += ["", f"Total: {total} PLN"]
    return "\n".join(lines)


def send_settlement_emails(calc: FeeCalculation) -> int:
    """Email each tenant their settlement breakdown (owner in CC). Returns count."""
    from apps.core.models import EmailTemplate
    from apps.core.services.mailer import get_template

    sent = 0
    # Subject may be customised via the owner's SETTLEMENT template.
    tpl_subject, _ = get_template(calc.owner, EmailTemplate.Kind.SETTLEMENT)
    subject = render_text(
        tpl_subject or "Utility settlement — {flat} ({period})",
        {"flat": str(calc.flat), "period": _period_label(calc)},
    )
    # Reuse one SMTP connection across the batch.
    connection, _ = owner_connection(calc.owner)

    # CC the account owner so they keep a copy of each tenant mailing.
    owner_cc = owner_address(calc.owner)
    cc = [owner_cc] if owner_cc else []

    for tenant in calc.tenants.all():
        if not tenant.email:
            continue
        send_owner_email(
            calc.owner,
            subject=subject,
            body=_body(tenant),
            to=[tenant.email],
            cc=cc,
            flat=calc.flat,
            connection=connection,
        )
        sent += 1
    return sent

