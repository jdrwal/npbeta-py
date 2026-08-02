"""Central outgoing-mail helper for landlord communication.

All landlord-initiated e-mail (settlements, contract-renewal reminders, ad-hoc
notifications) goes through :func:`send_owner_email`, which:

- uses the owner's own SMTP config (``MailSettings``) or the project default,
- writes an :class:`EmailLog` audit row,
- and enforces the addressing rules agreed with the product owner:
    * personalised mail (one per tenant) → ``to`` tenant, ``cc`` owner,
    * broadcast mail (same body to a whole flat) → ``to`` owner, ``bcc`` tenants
      (so tenants never see each other's addresses).
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.mail.backends.base import BaseEmailBackend

from apps.accounts.models import User
from apps.core.models import Contract, EmailLog, EmailTemplate, Flat

# Built-in defaults for the two standard template kinds. A stored active
# EmailTemplate of the same kind overrides these.
DEFAULT_TEMPLATES: dict[str, tuple[str, str]] = {
    EmailTemplate.Kind.CONTRACT_RENEWAL: (
        "Twoja umowa najmu wkrótce się kończy",
        "Dzień dobry {tenant_name},\n\n"
        "Umowa najmu {contract_number} dotycząca {flat} kończy się "
        "{contract_end}. Jeśli chcesz ją przedłużyć, daj proszę znać.\n\n"
        "Pozdrawiam,\n{owner_name}",
    ),
    EmailTemplate.Kind.SETTLEMENT: (
        "Rozliczenie mediów — {flat} ({period})",
        "Dzień dobry {tenant_name},\n\n"
        "W załączeniu rozliczenie mediów za okres {period} dla {flat}.\n\n"
        "Pozdrawiam,\n{owner_name}",
    ),
}


def owner_address(owner: User) -> str:
    """The owner's e-mail address (username doubles as login e-mail)."""
    email = owner.email or owner.get_username()
    return email if "@" in email else ""


def owner_connection(owner: User) -> tuple[BaseEmailBackend | None, str]:
    """Return (connection, from_email) for ``owner``.

    Uses the owner's custom SMTP when configured, otherwise the project default
    backend (``connection=None``).
    """
    mail_cfg = getattr(owner, "mail_settings", None)
    if mail_cfg is not None and mail_cfg.use_custom:
        connection = get_connection(
            host=mail_cfg.smtp_host,
            port=mail_cfg.smtp_port,
            username=mail_cfg.smtp_user or None,
            password=mail_cfg.smtp_password or None,
            use_tls=mail_cfg.use_tls,
            use_ssl=mail_cfg.use_ssl,
        )
        return connection, (mail_cfg.from_email or settings.DEFAULT_FROM_EMAIL)
    return None, settings.DEFAULT_FROM_EMAIL


def active_tenant_emails(flat: Flat) -> list[str]:
    """Distinct tenant e-mail addresses from all active contracts of ``flat``.

    Active = started on/before today and not yet ended (open-ended contracts,
    i.e. no end date, count as active).
    """
    from django.db.models import Q
    from django.utils import timezone

    today = timezone.now().date()
    contracts = Contract.objects.filter(flat=flat, contract_start__lte=today).filter(
        Q(contract_end__gte=today) | Q(contract_end__isnull=True)
    )
    seen: list[str] = []
    for email in contracts.values_list("email", flat=True):
        if email and email not in seen:
            seen.append(email)
    return seen


def render_text(text: str, context: dict[str, str]) -> str:
    """Fill ``{placeholder}`` tokens from ``context`` (unknown ones stay as-is)."""
    for key, value in context.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def get_template(owner: User, kind: str) -> tuple[str, str]:
    """Return (subject, body) for ``kind``: owner's active template or default."""
    tpl = (
        EmailTemplate.objects.filter(owner=owner, kind=kind, is_active=True)
        .order_by("-updated")
        .first()
    )
    if tpl is not None:
        return tpl.subject, tpl.body
    return DEFAULT_TEMPLATES.get(kind, ("", ""))


# Human-readable names for the seeded standard templates.
_DEFAULT_TEMPLATE_NAMES: dict[str, str] = {
    EmailTemplate.Kind.CONTRACT_RENEWAL: "Przedłużenie umowy",
    EmailTemplate.Kind.SETTLEMENT: "Rozliczenie / rachunek",
}


def ensure_default_templates(owner: User) -> None:
    """Create the standard templates as editable rows if the owner lacks them."""
    for kind, (subject, body) in DEFAULT_TEMPLATES.items():
        if not EmailTemplate.objects.filter(owner=owner, kind=kind).exists():
            EmailTemplate.objects.create(
                owner=owner,
                kind=kind,
                name=_DEFAULT_TEMPLATE_NAMES.get(kind, "Szablon"),
                subject=subject,
                body=body,
            )


def send_owner_email(
    owner: User,
    *,
    subject: str,
    body: str,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    flat: Flat | None = None,
    template: EmailTemplate | None = None,
    connection: BaseEmailBackend | None = None,
) -> EmailLog:
    """Send one e-mail on the owner's behalf and record an :class:`EmailLog`.

    ``connection`` may be passed to reuse one SMTP connection across a batch;
    otherwise a fresh one is opened from the owner's config.
    """
    to = to or []
    cc = cc or []
    bcc = bcc or []
    if connection is None:
        connection, from_email = owner_connection(owner)
    else:
        _, from_email = owner_connection(owner)

    log = EmailLog(
        owner=owner,
        flat=flat,
        template=template,
        subject=subject,
        body=body,
        to=to,
        cc=cc,
        bcc=bcc,
    )
    try:
        EmailMessage(
            subject=subject,
            body=body,
            from_email=from_email,
            to=to,
            cc=cc,
            bcc=bcc,
            connection=connection,
        ).send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - record the failure for the audit log
        log.status = EmailLog.Status.FAILED
        log.error = str(exc)
        log.save()
        raise
    log.status = EmailLog.Status.SENT
    log.save()
    return log


def send_flat_broadcast(
    owner: User, flat: Flat, subject: str, body: str,
    template: EmailTemplate | None = None,
) -> EmailLog | None:
    """Send one broadcast to all active tenants of ``flat`` (tenants in BCC).

    Owner goes in ``to`` (keeps a copy); tenant addresses are hidden in ``bcc``.
    Returns the log, or ``None`` when the flat has no tenant addresses.
    """
    tenants = active_tenant_emails(flat)
    if not tenants:
        return None
    owner_to = owner_address(owner)
    return send_owner_email(
        owner,
        subject=subject,
        body=body,
        to=[owner_to] if owner_to else [],
        bcc=tenants,
        flat=flat,
        template=template,
    )
