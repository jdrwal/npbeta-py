"""Tests for landlord e-mail communication: templates, ad-hoc broadcast, mailer."""

from datetime import date, timedelta

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Contract, EmailLog, EmailTemplate, Flat, Room
from apps.core.services.mailer import (
    active_tenant_emails,
    render_text,
    send_flat_broadcast,
)


@pytest.fixture
def landlord(db: None) -> User:
    return User.objects.create_user(username="owner@example.com", password="pw")


@pytest.fixture
def flat_with_tenants(landlord: User) -> Flat:
    flat = Flat.objects.create(owner=landlord, city="C", street="S", code="X")
    room = Room.objects.create(owner=landlord, flat=flat, room_no=1, beds=1)
    today = timezone.now().date()
    Contract.objects.create(
        owner=landlord, flat=flat, room=room, tenant_name="A", email="a@example.com",
        contract_start=today - timedelta(days=10), contract_end=today + timedelta(days=10),
    )
    Contract.objects.create(
        owner=landlord, flat=flat, room=room, tenant_name="B", email="b@example.com",
        contract_start=today - timedelta(days=10), contract_end=None,
    )
    # Expired contract — must be excluded.
    Contract.objects.create(
        owner=landlord, flat=flat, room=room, tenant_name="C", email="c@example.com",
        contract_start=date(2000, 1, 1), contract_end=date(2000, 2, 1),
    )
    return flat


def test_render_text_replaces_known_tokens() -> None:
    out = render_text("Cześć {tenant_name}, {flat}", {"tenant_name": "Jan", "flat": "M1"})
    assert out == "Cześć Jan, M1"


@pytest.mark.django_db
def test_active_tenant_emails_only_active(flat_with_tenants: Flat) -> None:
    emails = active_tenant_emails(flat_with_tenants)
    assert emails == ["a@example.com", "b@example.com"]


@pytest.mark.django_db
def test_send_flat_broadcast_bcc_and_owner_to(
    landlord: User, flat_with_tenants: Flat
) -> None:
    log = send_flat_broadcast(landlord, flat_with_tenants, "Temat", "Treść")
    assert log is not None
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["owner@example.com"]
    assert sorted(msg.bcc) == ["a@example.com", "b@example.com"]
    assert log.status == EmailLog.Status.SENT


@pytest.mark.django_db
def test_broadcast_none_without_tenants(landlord: User) -> None:
    flat = Flat.objects.create(owner=landlord, city="C", street="S", code="Y")
    assert send_flat_broadcast(landlord, flat, "s", "b") is None
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_template_crud(landlord: User) -> None:
    client = Client()
    client.force_login(landlord)
    resp = client.post(
        reverse("core:email_template_add"),
        {
            "kind": EmailTemplate.Kind.CUSTOM,
            "name": "Powiadomienie",
            "subject": "Cześć",
            "body": "Treść {flat}",
            "is_active": "on",
        },
    )
    assert resp.status_code == 302
    tpl = EmailTemplate.objects.get(owner=landlord)
    assert tpl.name == "Powiadomienie"

    client.post(
        reverse("core:email_template_delete", args=[tpl.pk])
    )
    assert EmailTemplate.objects.filter(pk=tpl.pk).count() == 0


@pytest.mark.django_db
def test_send_adhoc_enqueues_and_sends(
    landlord: User, flat_with_tenants: Flat
) -> None:
    client = Client()
    client.force_login(landlord)
    resp = client.post(
        reverse("core:send_adhoc"),
        {"flat": flat_with_tenants.pk, "subject": "Awaria wody", "body": "Jutro."},
    )
    assert resp.status_code == 302
    assert len(mail.outbox) == 1
    assert sorted(mail.outbox[0].bcc) == ["a@example.com", "b@example.com"]


@pytest.mark.django_db
def test_communication_seeds_default_templates(landlord: User) -> None:
    client = Client()
    client.force_login(landlord)
    resp = client.get(reverse("core:communication"))
    assert resp.status_code == 200
    kinds = set(
        EmailTemplate.objects.filter(owner=landlord).values_list("kind", flat=True)
    )
    assert EmailTemplate.Kind.CONTRACT_RENEWAL in kinds
    assert EmailTemplate.Kind.SETTLEMENT in kinds
    # Idempotent: a second visit does not duplicate them.
    client.get(reverse("core:communication"))
    assert EmailTemplate.objects.filter(owner=landlord).count() == 2


@pytest.mark.django_db
def test_template_editor_shows_tags_and_preview(landlord: User) -> None:
    tpl = EmailTemplate.objects.create(
        owner=landlord, kind=EmailTemplate.Kind.CUSTOM,
        name="X", subject="Cześć {tenant_name}", body="Treść",
    )
    client = Client()
    client.force_login(landlord)
    resp = client.get(reverse("core:email_template_edit", args=[tpl.pk]))
    assert resp.status_code == 200
    assert b"{tenant_name}" in resp.content  # tag chip
    assert b'id="preview-body"' in resp.content  # live preview pane


@pytest.mark.django_db
def test_template_owner_scoped(landlord: User) -> None:
    other = User.objects.create_user(username="other@example.com", password="pw")
    tpl = EmailTemplate.objects.create(
        owner=other, kind=EmailTemplate.Kind.CUSTOM, name="X", subject="s", body="b"
    )
    client = Client()
    client.force_login(landlord)
    assert client.get(
        reverse("core:email_template_edit", args=[tpl.pk])
    ).status_code == 404


@pytest.mark.django_db
def test_template_restore_resets_to_default(landlord: User) -> None:
    from apps.core.services.mailer import DEFAULT_TEMPLATES

    tpl = EmailTemplate.objects.create(
        owner=landlord, kind=EmailTemplate.Kind.SETTLEMENT,
        name="Rozliczenie", subject="stary temat", body="ręcznie zmieniona treść",
    )
    client = Client()
    client.force_login(landlord)
    resp = client.post(reverse("core:email_template_restore", args=[tpl.pk]))
    assert resp.status_code == 302
    tpl.refresh_from_db()
    subject, body = DEFAULT_TEMPLATES[EmailTemplate.Kind.SETTLEMENT]
    assert tpl.subject == subject
    assert tpl.body == body


@pytest.mark.django_db
def test_template_restore_button_only_when_default_exists(landlord: User) -> None:
    client = Client()
    client.force_login(landlord)
    settlement = EmailTemplate.objects.create(
        owner=landlord, kind=EmailTemplate.Kind.SETTLEMENT, name="S", subject="s", body="b"
    )
    custom = EmailTemplate.objects.create(
        owner=landlord, kind=EmailTemplate.Kind.CUSTOM, name="C", subject="s", body="b"
    )
    r_settlement = client.get(reverse("core:email_template_edit", args=[settlement.pk]))
    r_custom = client.get(reverse("core:email_template_edit", args=[custom.pk]))
    assert b"Przywr" in r_settlement.content  # restore button for settlement
    assert b"Przywr" not in r_custom.content  # no default -> no button


@pytest.mark.django_db
def test_email_reply_to_defaults_to_account(
    landlord: User, flat_with_tenants: Flat
) -> None:
    send_flat_broadcast(landlord, flat_with_tenants, "Temat", "Treść")
    assert mail.outbox[0].reply_to == ["owner@example.com"]


@pytest.mark.django_db
def test_email_reply_to_custom_override(
    landlord: User, flat_with_tenants: Flat
) -> None:
    from apps.accounts.models import MailSettings

    MailSettings.objects.update_or_create(
        user=landlord, defaults={"reply_to": "kontakt@przyklad.pl"}
    )
    send_flat_broadcast(landlord, flat_with_tenants, "Temat", "Treść")
    assert mail.outbox[0].reply_to == ["kontakt@przyklad.pl"]


@pytest.mark.django_db
def test_test_mode_redirects_all_mail_to_owner(
    landlord: User, flat_with_tenants: Flat
) -> None:
    from apps.accounts.models import MailSettings

    MailSettings.objects.update_or_create(
        user=landlord, defaults={"test_mode": True}
    )
    send_flat_broadcast(landlord, flat_with_tenants, "Temat", "Treść")
    msg = mail.outbox[0]
    assert msg.to == ["owner@example.com"]  # redirected to the owner
    assert msg.bcc == []  # tenants no longer receive it
    assert msg.subject.startswith("[TEST]")
    assert "[TRYB TESTOWY]" in msg.body
    assert "a@example.com" in msg.body  # original recipients listed in the notice


@pytest.mark.django_db
def test_contract_send_renewal(landlord: User) -> None:
    flat = Flat.objects.create(owner=landlord, city="C", street="S", code="Z")
    room = Room.objects.create(owner=landlord, flat=flat, room_no=1, beds=1)
    today = timezone.now().date()
    contract = Contract.objects.create(
        owner=landlord, flat=flat, room=room, tenant_name="T",
        email="t@example.com", contract_number="Z/1",
        contract_start=today - timedelta(days=100),
        contract_end=today + timedelta(days=15),
    )
    client = Client()
    client.force_login(landlord)

    # Preview must not send.
    prev = client.get(reverse("core:contract_renewal_preview", args=[contract.pk]))
    assert prev.status_code == 200
    data = prev.json()
    assert "Z/1" in data["body"]
    assert "wygasa z dniem" in data["body"]  # formal wording
    assert len(mail.outbox) == 0

    # Send returns JSON and mails the tenant with owner reply-to.
    resp = client.post(reverse("core:contract_send_renewal", args=[contract.pk]))
    assert resp.status_code == 200
    assert resp.json()["sent"] is True
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["t@example.com"]
    assert mail.outbox[0].reply_to == ["owner@example.com"]
    assert "Z/1" in mail.outbox[0].body
