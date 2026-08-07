"""Email notifications for settlements (the legacy sendMail was a stub)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.mail.backends.base import BaseEmailBackend

from apps.core.models import Contract, FeeCalculation, FeeCalculationTenant
from apps.core.services.mailer import (
    owner_connection,
    render_text,
    send_owner_email,
)


def _period_label(calc: FeeCalculation) -> str:
    return f"{calc.period_start:%Y-%m-%d} – {calc.period_end:%Y-%m-%d}"


def _items_block(tenant: FeeCalculationTenant) -> tuple[str, Decimal]:
    """Per-section subtotals and the grand total for one tenant.

    Rather than listing every line, the tenant sees one number per settlement
    section (meter fees, other fees, funds); empty sections are omitted.
    """
    sections = (
        ("Counter", "Opłaty licznikowe"),
        ("Admin", "Opłaty pozostałe"),
        ("Fund", "Fundusze"),
    )
    sums: dict[str, Decimal] = {}
    total = Decimal(0)
    for item in tenant.items.all():
        sums[item.fee_type] = sums.get(item.fee_type, Decimal(0)) + item.value
        total += item.value
    lines = [
        f"{label}: {sums[key]:.2f} zł" for key, label in sections if key in sums
    ]
    return "\n".join(lines), total


def _fallback_body(tenant: FeeCalculationTenant) -> str:
    """Built-in body, used only when the template body renders empty."""
    from apps.core.services.mailer import room_label

    calc = tenant.calculation
    items, total = _items_block(tenant)
    room = room_label(tenant.contract.room if tenant.contract else None)
    room_line = f"Pokój: {room}\n" if room else ""
    return (
        f"Rozliczenie opłat — {calc.flat}\n"
        f"{room_line}"
        f"Okres: {_period_label(calc)}\n\n"
        f"Do zapłaty razem: {total:.2f} zł\n\n"
        f"W tym:\n{items}"
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
    from apps.core.services.mailer import get_template, room_label

    subject_tpl, body_tpl = get_template(calc.owner, EmailTemplate.Kind.SETTLEMENT)
    owner = calc.owner
    owner_name = owner.get_full_name() or owner.get_username()
    items, total = _items_block(tenant)
    context = {
        "tenant_name": tenant.tenant_name or "",
        "contract_number": tenant.contract_number or "",
        "flat": str(calc.flat),
        "room": room_label(tenant.contract.room if tenant.contract else None),
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
) -> bool:
    """Send the settlement email to a single tenant. Returns False if no address.

    The landlord's copy is the flat's hidden BCC (never a visible CC), so the
    tenant never sees the owner's address. ``connection`` may be reused across
    a batch.
    """
    if not tenant.email:
        return False
    subject, body = render_settlement_email(calc, tenant)
    send_owner_email(
        calc.owner,
        subject=subject,
        body=body,
        to=[tenant.email],
        flat=calc.flat,
        settlement_tenant=tenant,
        connection=connection,
    )
    return True


def send_settlement_emails(calc: FeeCalculation) -> int:
    """Email each tenant their settlement breakdown. Returns the count sent.

    Both the subject and the body come from the owner's SETTLEMENT template
    (falling back to the built-in default). The per-tenant fee breakdown and
    total are injected via the ``{items}`` and ``{total}`` placeholders. The
    landlord's copy is the flat's hidden BCC, not a visible CC.
    """
    owner = calc.owner
    connection, _ = owner_connection(owner)
    sent = 0
    for tenant in calc.tenants.all():
        if send_settlement_email_to(calc, tenant, connection=connection):
            sent += 1
    return sent


def _renewal_fallback(contract: Contract, renew_until: str) -> str:
    """Built-in renewal proposal body, used when the template renders empty."""
    from apps.core.services.mailer import room_label

    owner = contract.owner
    room = room_label(contract.room)
    place = f"{contract.flat}, {room}" if room else str(contract.flat)
    return (
        "Dzień dobry,\n\n"
        f"proponujemy przedłużenie umowy najmu {contract.contract_number} "
        f"dot. {place} do dnia {renew_until}. Prosimy o informację, czy "
        "akceptują Państwo nowy termin.\n\n"
        f"Pozdrawiam,\n{owner.get_full_name() or owner.get_username()}"
    )


def render_renewal_email(
    contract: Contract, *, renew_until: date | None = None
) -> tuple[str, str]:
    """Rendered (subject, body) of the contract-renewal proposal for a tenant.

    ``renew_until`` is the proposed new end date; when omitted the current
    contract end is used. The body excludes the marketing footer (added by the
    send path).
    """
    from apps.core.models import EmailTemplate
    from apps.core.services.mailer import get_template, room_label

    subject_tpl, body_tpl = get_template(
        contract.owner, EmailTemplate.Kind.CONTRACT_RENEWAL
    )
    owner = contract.owner
    target = renew_until or contract.contract_end
    renew_until_str = target.isoformat() if target else ""
    context = {
        "tenant_name": contract.tenant_name or "",
        "contract_number": contract.contract_number or "",
        "flat": str(contract.flat),
        "room": room_label(contract.room),
        "contract_end": (
            contract.contract_end.isoformat() if contract.contract_end else ""
        ),
        "renew_until": renew_until_str,
        "payment_day": str(contract.payment_day or ""),
        "owner_name": owner.get_full_name() or owner.get_username(),
    }
    subject = render_text(
        subject_tpl or "Propozycja przedłużenia umowy {contract_number}",
        context,
    )
    body = render_text(body_tpl or "", context).strip()
    if not body:
        body = _renewal_fallback(contract, renew_until_str)
    return subject, body


def send_renewal_email(
    contract: Contract, *, renew_until: date | None = None
) -> bool:
    """Send the renewal proposal to the tenant (owner as hidden BCC). False if no address."""
    if not contract.email:
        return False
    subject, body = render_renewal_email(contract, renew_until=renew_until)
    send_owner_email(
        contract.owner,
        subject=subject,
        body=body,
        to=[contract.email],
        flat=contract.flat,
    )
    return True

