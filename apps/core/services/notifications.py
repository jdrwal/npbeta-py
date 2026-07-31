"""Email notifications for settlements (the legacy sendMail was a stub)."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.mail import get_connection, send_mail

from apps.core.models import FeeCalculation, FeeCalculationTenant


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
    """Email each tenant with an address their settlement breakdown. Returns count."""
    sent = 0
    subject = (
        f"Utility settlement — {calc.flat} "
        f"({calc.period_start:%Y-%m-%d} – {calc.period_end:%Y-%m-%d})"
    )
    # Prefer the owner's custom SMTP; fall back to the project default backend.
    mail_cfg = getattr(calc.owner, "mail_settings", None)
    if mail_cfg is not None and mail_cfg.use_custom:
        connection = get_connection(
            host=mail_cfg.smtp_host,
            port=mail_cfg.smtp_port,
            username=mail_cfg.smtp_user or None,
            password=mail_cfg.smtp_password or None,
            use_tls=mail_cfg.use_tls,
            use_ssl=mail_cfg.use_ssl,
        )
        from_email = mail_cfg.from_email or settings.DEFAULT_FROM_EMAIL
    else:
        connection = None
        from_email = settings.DEFAULT_FROM_EMAIL

    for tenant in calc.tenants.all():
        if not tenant.email:
            continue
        send_mail(
            subject=subject,
            message=_body(tenant),
            from_email=from_email,
            recipient_list=[tenant.email],
            fail_silently=False,
            connection=connection,
        )
        sent += 1
    return sent
