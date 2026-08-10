"""Central outgoing-mail helper for landlord communication.

All landlord-initiated e-mail (settlements, contract-renewal reminders, ad-hoc
notifications) goes through :func:`send_owner_email`, which:

- uses the owner's own SMTP config (``MailSettings``) or the project default,
- writes an :class:`EmailLog` audit row,
- and enforces the addressing rules agreed with the product owner:
    * the owner's own copy is ALWAYS a hidden ``bcc`` (never a visible ``cc``):
      the flat's ``owner_bcc_email`` if set, otherwise the owner's login e-mail,
    * broadcast mail (same body to a whole flat) → ``to`` owner, ``bcc`` tenants
      (so tenants never see each other's addresses).
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.mail.backends.base import BaseEmailBackend

from apps.accounts.models import User
from apps.core.models import (
    Contract,
    EmailLog,
    EmailTemplate,
    FeeCalculationTenant,
    Flat,
    Room,
)

# Built-in defaults for the two standard template kinds. A stored active
# EmailTemplate of the same kind overrides these.
DEFAULT_TEMPLATES: dict[str, tuple[str, str]] = {
    EmailTemplate.Kind.CONTRACT_RENEWAL: (
        "Propozycja przedłużenia umowy {contract_number}",
        "Dzień dobry,\n\n"
        "Bieżąca umowa najmu {contract_number} wygasa w dniu {contract_end}. "
        "Proszę o potwierdzenie przedłużenia najmu do dnia {renew_until}.\n\n"
        "Pozdrawiam,\n{owner_name}",
    ),
    EmailTemplate.Kind.PAYMENT_REMINDER: (
        "Rabat za terminową płatność / On-time payment discount",
        "Witam,\n\n"
        "Płatność za bieżący okres do tej pory do mnie nie dotarła, proszę o weryfikację "
        "jeśli przelew został zlecony.\n"
        "W przeciwnym wypadku zachęcam do skorzystania z rabatu za terminową płatność — "
        "dla przypomnienia przelew powinien dotrzeć najpóźniej do {payment_day} dnia miesiąca.\n\n"
        "---\n\n"
        "Hello,\n\n"
        "I have not received payment for current month yet. Please verify your bank transfer "
        "status if it has been already ordered.\n"
        "Otherwise I encourage you to benefit from on-time payment discount — in order to be "
        "eligible your transfer needs to be delivered to my bank account until {payment_day}th day "
        "of the month.\n\n"
        "pozdrawiam / best regards,\n{owner_name}",
    ),
    EmailTemplate.Kind.SETTLEMENT: (
        "Rozliczenie mediów — {flat} ({period})",
        "Dzień dobry,\n\n"
        "rozliczenie opłat za okres {period} — {flat}, {room}.\n\n"
        "Do zapłaty razem: {total}\n\n"
        "W tym:\n{items}\n\n"
        "Pozdrawiam,\n{owner_name}",
    ),
}

# Placeholders usable in templates, with a human description and a sample value
# used for the live preview. ``{owner_name}`` sample is filled per request.
TEMPLATE_TAGS: list[tuple[str, str, str]] = [
    ("{tenant_name}", "Imię i nazwisko najemcy (mianownik, bez odmiany)", "Jan Kowalski"),
    ("{flat}", "Adres lokalu / obiektu", "ul. Przykładowa 1/2, Warszawa"),
    ("{room}", "Pokój", "Pokój 1"),
    ("{contract_number}", "Numer umowy", "L123/2026/01"),
    ("{contract_end}", "Data zakończenia umowy", "2026-12-31"),
    ("{renew_until}", "Proponowany nowy termin", "2027-12-31"),
    ("{period}", "Okres rozliczenia", "2026-07"),
    ("{items}", "Podsumowanie rozliczenia (sumy sekcji)",
     "Opłaty licznikowe: 180.39 zł\nOpłaty pozostałe: 240.00 zł\nFundusze: 25.00 zł"),
    ("{total}", "Suma rozliczenia", "205.39 zł"),
    ("{payment_day}", "Dzień płatności", "10"),
    ("{owner_name}", "Twoje imię / nazwa", "Właściciel"),
]


def preview_context(owner: User) -> dict[str, str]:
    """Sample values for rendering a template preview."""
    ctx = {tag.strip("{}"): sample for tag, _desc, sample in TEMPLATE_TAGS}
    ctx["owner_name"] = owner.get_full_name() or owner.get_username()
    return ctx


def owner_address(owner: User) -> str:
    """The owner's e-mail address (username doubles as login e-mail)."""
    email = owner.email or owner.get_username()
    return email if "@" in email else ""


def room_label(room: Room | None) -> str:
    """Short room label for e-mails (no flat suffix): name or ``Pokój <no>``."""
    if room is None:
        return ""
    if room.name:
        return room.name
    if room.room_no:
        return f"Pokój {room.room_no}"
    return ""


def owner_reply_to(owner: User) -> list[str]:
    """Reply-To for the owner's mail: their configured address, else account e-mail."""
    mail_cfg = getattr(owner, "mail_settings", None)
    configured = getattr(mail_cfg, "reply_to", "") if mail_cfg is not None else ""
    addr = configured or owner_address(owner)
    return [addr] if addr and "@" in addr else []


def owner_test_mode(owner: User) -> bool:
    """Whether the owner has SMTP test mode on (redirect all mail to self)."""
    mail_cfg = getattr(owner, "mail_settings", None)
    return bool(getattr(mail_cfg, "test_mode", False))


def owner_test_recipient(owner: User) -> str:
    """Address that test-mode mail is redirected to (configured, else account)."""
    mail_cfg = getattr(owner, "mail_settings", None)
    configured = getattr(mail_cfg, "test_recipient", "") if mail_cfg else ""
    return configured or owner_address(owner)


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


# Marketing footer appended to every outgoing e-mail (branding).
MARKETING_FOOTER = (
    "—\n"
    "Ta wiadomość została przygotowana i wysłana za pomocą Rozlicz Najem\n"
    "http://rozlicz-najem.pl — prosty sposób na rozliczanie najmu."
)


def with_footer(body: str) -> str:
    """Append the standard marketing footer to an e-mail body."""
    return f"{(body or '').rstrip()}\n\n{MARKETING_FOOTER}\n"



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
    EmailTemplate.Kind.PAYMENT_REMINDER: "Ponaglenie o płatność",
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
    bcc: list[str] | None = None,
    flat: Flat | None = None,
    template: EmailTemplate | None = None,
    settlement_tenant: FeeCalculationTenant | None = None,
    connection: BaseEmailBackend | None = None,
) -> EmailLog:
    """Send one e-mail on the owner's behalf and record an :class:`EmailLog`.

    The owner's copy is ALWAYS a hidden BCC (never a CC): the flat's configured
    ``owner_bcc_email`` if set, otherwise the owner's login e-mail. ``connection``
    may be passed to reuse one SMTP connection across a batch.
    """
    to = to or []
    bcc = bcc or []
    # Owner's own copy — always BCC, never CC; flat address if set, else login.
    owner_copy = (
        flat.owner_bcc_email if flat is not None and flat.owner_bcc_email
        else owner_address(owner)
    )
    if owner_copy and owner_copy not in to and owner_copy not in bcc:
        bcc = [*bcc, owner_copy]
    body = with_footer(body)
    reply_to = owner_reply_to(owner)
    if connection is None:
        connection, from_email = owner_connection(owner)
    else:
        _, from_email = owner_connection(owner)

    # Test mode: redirect everything to the owner so no tenant is mailed.
    if owner_test_mode(owner):
        redirect_to = owner_test_recipient(owner) or (reply_to[0] if reply_to else "")
        if redirect_to:
            intended = []
            if to:
                intended.append("To: " + ", ".join(to))
            if bcc:
                intended.append("BCC: " + ", ".join(bcc))
            notice = (
                "[TRYB TESTOWY] W zwykłym trybie ta wiadomość trafiłaby do:\n"
                + ("\n".join("  " + line for line in intended) or "  (brak odbiorców)")
                + "\n\n"
            )
            subject = "[TEST] " + subject
            body = notice + body
            to, bcc = [redirect_to], []

    log = EmailLog(
        owner=owner,
        flat=flat,
        template=template,
        settlement_tenant=settlement_tenant,
        subject=subject,
        body=body,
        to=to,
        cc=[],
        bcc=bcc,
    )
    try:
        EmailMessage(
            subject=subject,
            body=body,
            from_email=from_email,
            to=to,
            cc=[],
            bcc=bcc,
            reply_to=reply_to,
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
