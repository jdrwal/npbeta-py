"""Sign-up forms for the open platform (landlord + tenant self-registration)."""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from apps.core.models import ContractInvite

User = get_user_model()

_PW_WIDGET = forms.PasswordInput(attrs={"autocomplete": "new-password"})


class _BaseSignupForm(forms.Form):
    """Shared e-mail + password sign-up logic; subclasses set the role."""

    role: str = User.Role.LANDLORD

    first_name = forms.CharField(
        label="Imię i nazwisko", max_length=150, required=False
    )
    email = forms.EmailField(label="E-mail")
    password1 = forms.CharField(
        label="Hasło",
        widget=_PW_WIDGET,
        help_text=(
            "Co najmniej 8 znaków, nie może być zbyt podobne do e-maila, "
            "zbyt popularne ani wyłącznie z cyfr."
        ),
    )
    password2 = forms.CharField(label="Powtórz hasło", widget=_PW_WIDGET)

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        taken = (
            User.objects.filter(username__iexact=email).exists()
            or User.objects.filter(email__iexact=email).exists()
        )
        if taken:
            raise forms.ValidationError("Konto z tym adresem e-mail już istnieje.")
        return email

    def clean_password2(self) -> str:
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Hasła nie są identyczne.")
        return cast(str, p2 or "")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        p1 = cleaned.get("password1")
        if p1:
            probe = User(
                username=cleaned.get("email", ""),
                email=cleaned.get("email", ""),
                first_name=cleaned.get("first_name", ""),
            )
            try:
                validate_password(p1, probe)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def create_user(self) -> Any:
        email = self.cleaned_data["email"]
        user = User(
            username=email,
            email=email,
            first_name=self.cleaned_data.get("first_name", ""),
            role=self.role,
            is_active=False,
            email_verified=False,
        )
        user.set_password(self.cleaned_data["password1"])
        user.save()
        return user


class LandlordSignupForm(_BaseSignupForm):
    role = User.Role.LANDLORD


class ResendActivationForm(forms.Form):
    """Ask for an email to re-send the activation link to."""

    email = forms.EmailField(label="E-mail")


class TenantSignupForm(_BaseSignupForm):
    role = User.Role.TENANT

    invite_code = forms.CharField(
        label="Kod zaproszenia",
        help_text="Kod lub link otrzymany od wynajmującego.",
    )

    def clean_invite_code(self) -> str:
        code = self.cleaned_data["invite_code"].strip()
        try:
            invite = ContractInvite.objects.get(token=code)
        except ContractInvite.DoesNotExist:
            raise forms.ValidationError("Nieprawidłowy kod zaproszenia.") from None
        if invite.is_used:
            raise forms.ValidationError("To zaproszenie zostało już wykorzystane.")
        self._invite = invite
        return code

    def create_user(self) -> Any:
        user = super().create_user()
        invite = self._invite
        invite.accepted_by = user
        invite.accepted_at = timezone.now()
        invite.save(update_fields=["accepted_by", "accepted_at"])
        contract = invite.contract
        contract.tenant_user = user
        contract.save(update_fields=["tenant_user"])
        return user
