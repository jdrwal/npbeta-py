"""Auth tests: legacy MD5 passwords verify and upgrade to Argon2 on login."""

import hashlib

import pytest
from django.contrib.auth import authenticate, get_user_model


@pytest.mark.django_db
def test_legacy_md5_login_upgrades_to_argon2() -> None:
    digest = hashlib.md5(b"secret").hexdigest()  # noqa: S324 (legacy compat)
    user_model = get_user_model()
    user = user_model.objects.create(
        username="tenant@example.com",
        email="tenant@example.com",
        password=f"legacy_md5${digest}",
    )

    authenticated = authenticate(username="tenant@example.com", password="secret")
    assert authenticated is not None

    user.refresh_from_db()
    assert user.password.startswith("argon2")
    assert not user.password.startswith("legacy_md5")


@pytest.mark.django_db
def test_legacy_md5_rejects_wrong_password() -> None:
    digest = hashlib.md5(b"secret").hexdigest()  # noqa: S324
    get_user_model().objects.create(
        username="tenant2@example.com",
        email="tenant2@example.com",
        password=f"legacy_md5${digest}",
    )
    assert authenticate(username="tenant2@example.com", password="wrong") is None
