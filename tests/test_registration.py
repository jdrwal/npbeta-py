"""Tests for open registration, email activation, roles, and the tenant portal."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.models import Contract, ContractInvite, Flat, Room

User = get_user_model()

STRONG_PW = "Trawa-9284-Xylo"


def _landlord() -> object:
    return User.objects.create_user(
        username="owner@example.com",
        email="owner@example.com",
        password=STRONG_PW,
        role=User.Role.LANDLORD,
    )


def _flat_room(owner: object) -> tuple[Flat, Room]:
    flat = Flat.objects.create(owner=owner, city="Kraków", street="Główna", building_no="1")
    room = Room.objects.create(owner=owner, flat=flat, room_no=1, beds=1)
    return flat, room


@pytest.mark.django_db
def test_landlord_registration_sends_activation_and_activates() -> None:
    client = Client()
    resp = client.post(
        reverse("accounts:register_landlord"),
        {
            "first_name": "Jan Nowak",
            "email": "New@Example.com",
            "password1": STRONG_PW,
            "password2": STRONG_PW,
        },
    )
    assert resp.status_code == 200
    user = User.objects.get(email="new@example.com")
    assert user.role == User.Role.LANDLORD
    assert user.is_active is False
    assert user.email_verified is False
    assert len(mail.outbox) == 1

    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    resp = client.get(reverse("accounts:activate", args=[uidb64, token]))
    assert resp.status_code == 302
    user.refresh_from_db()
    assert user.is_active is True
    assert user.email_verified is True


@pytest.mark.django_db
def test_resend_activation_for_inactive_and_quiet_for_unknown() -> None:
    User.objects.create_user(
        username="pending@example.com",
        email="pending@example.com",
        password=STRONG_PW,
        role=User.Role.LANDLORD,
        is_active=False,
    )
    client = Client()
    resp = client.post(
        reverse("accounts:resend_activation"), {"email": "Pending@example.com"}
    )
    assert resp.status_code == 200
    assert len(mail.outbox) == 1

    mail.outbox.clear()
    # Unknown address: same page, no email sent (no account enumeration).
    resp = client.post(
        reverse("accounts:resend_activation"), {"email": "nobody@example.com"}
    )
    assert resp.status_code == 200
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_duplicate_email_rejected() -> None:
    _landlord()
    client = Client()
    resp = client.post(
        reverse("accounts:register_landlord"),
        {"email": "owner@example.com", "password1": STRONG_PW, "password2": STRONG_PW},
    )
    assert resp.status_code == 200
    assert b"ju\xc5\xbc istnieje" in resp.content


@pytest.mark.django_db
def test_tenant_registration_with_invite_links_contract() -> None:
    owner = _landlord()
    flat, room = _flat_room(owner)
    contract = Contract.objects.create(
        owner=owner, flat=flat, room=room, contract_number="A/1", tenant_name="X"
    )
    invite = ContractInvite.objects.create(contract=contract)

    client = Client()
    resp = client.post(
        reverse("accounts:register_tenant"),
        {
            "invite_code": invite.token,
            "email": "tenant@example.com",
            "password1": STRONG_PW,
            "password2": STRONG_PW,
        },
    )
    assert resp.status_code == 200
    tenant = User.objects.get(email="tenant@example.com")
    assert tenant.role == User.Role.TENANT
    contract.refresh_from_db()
    assert contract.tenant_user_id == tenant.pk
    invite.refresh_from_db()
    assert invite.accepted_by_id == tenant.pk


@pytest.mark.django_db
def test_used_invite_rejected() -> None:
    owner = _landlord()
    flat, room = _flat_room(owner)
    contract = Contract.objects.create(owner=owner, flat=flat, room=room)
    other = User.objects.create_user(
        username="t0@example.com", email="t0@example.com", password=STRONG_PW,
        role=User.Role.TENANT,
    )
    invite = ContractInvite.objects.create(contract=contract, accepted_by=other)

    client = Client()
    resp = client.post(
        reverse("accounts:register_tenant"),
        {
            "invite_code": invite.token,
            "email": "tenant2@example.com",
            "password1": STRONG_PW,
            "password2": STRONG_PW,
        },
    )
    assert resp.status_code == 200
    assert not User.objects.filter(email="tenant2@example.com").exists()


@pytest.mark.django_db
def test_role_routing_and_access_control() -> None:
    owner = _landlord()
    tenant = User.objects.create_user(
        username="tn@example.com", email="tn@example.com", password=STRONG_PW,
        role=User.Role.TENANT,
    )

    # Tenant is redirected away from landlord pages to the portal.
    client = Client()
    client.force_login(tenant)
    assert client.get(reverse("core:dashboard")).url == reverse("core:portal")
    assert client.get(reverse("core:portal")).status_code == 200

    # Landlord is redirected out of the portal to the dashboard.
    client2 = Client()
    client2.force_login(owner)
    assert client2.get(reverse("core:portal")).url == reverse("core:dashboard")
    assert client2.get(reverse("core:dashboard")).status_code == 200


@pytest.mark.django_db
def test_tenant_portal_shows_only_own_data() -> None:
    owner = _landlord()
    flat, room = _flat_room(owner)
    tenant = User.objects.create_user(
        username="mine@example.com", email="mine@example.com", password=STRONG_PW,
        role=User.Role.TENANT,
    )
    Contract.objects.create(
        owner=owner, flat=flat, room=room, contract_number="M/1",
        tenant_name="Mine", tenant_user=tenant, price=Decimal("1000"),
        contract_start=date(2025, 1, 1),
    )
    Contract.objects.create(
        owner=owner, flat=flat, room=room, contract_number="OTHER", tenant_name="Other"
    )

    client = Client()
    client.force_login(tenant)
    resp = client.get(reverse("core:portal"))
    assert resp.status_code == 200
    assert b"M/1" in resp.content
    assert b"OTHER" not in resp.content
