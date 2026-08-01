"""Account views: open self-registration (landlord + tenant) with email activation."""

from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from apps.accounts.forms import (
    LandlordSignupForm,
    ResendActivationForm,
    TenantSignupForm,
)
from apps.accounts.models import User as UserType
from apps.accounts.tasks import send_activation_email_task

User = get_user_model()


def _send_activation_email(request: HttpRequest, user: UserType) -> None:
    """Build a one-time activation link and queue the email on a worker."""
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("accounts:activate", args=[uidb64, token])
    link = request.build_absolute_uri(path)
    send_activation_email_task.delay(user.pk, link)


def _register(
    request: HttpRequest, form_class: type, template: str, **initial: str
) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("accounts:post_login")
    if request.method == "POST":
        form = form_class(request.POST)
        if form.is_valid():
            user = form.create_user()
            _send_activation_email(request, user)
            return render(request, "registration/registration_done.html", {"email": user.email})
    else:
        form = form_class(initial=initial)
    return render(request, template, {"form": form})


def register_choice(request: HttpRequest) -> HttpResponse:
    """Landing page: pick landlord or tenant registration."""
    if request.user.is_authenticated:
        return redirect("accounts:post_login")
    return render(request, "registration/register_choice.html")


def register_landlord(request: HttpRequest) -> HttpResponse:
    return _register(request, LandlordSignupForm, "registration/register_landlord.html")


def register_tenant(request: HttpRequest) -> HttpResponse:
    invite = request.GET.get("invite", "")
    return _register(
        request, TenantSignupForm, "registration/register_tenant.html", invite_code=invite
    )


def resend_activation(request: HttpRequest) -> HttpResponse:
    """Re-send the activation link. Never reveals whether the email exists."""
    if request.user.is_authenticated:
        return redirect("accounts:post_login")
    form = ResendActivationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email, is_active=False).first()
        if user is not None:
            _send_activation_email(request, user)
        return render(
            request,
            "registration/registration_done.html",
            {"email": email, "resent": True},
        )
    return render(request, "registration/resend_activation.html", {"form": form})


def activate(request: HttpRequest, uidb64: str, token: str) -> HttpResponse:
    """Confirm an account's email via the one-time activation link."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is not None and default_token_generator.check_token(user, token):
        if not user.is_active:
            user.is_active = True
            user.email_verified = True
            user.save(update_fields=["is_active", "email_verified"])
        messages.success(request, "Konto zostało aktywowane. Możesz się zalogować.")
        return redirect("login")
    return render(request, "registration/activation_invalid.html", status=400)


@login_required
def post_login(request: HttpRequest) -> HttpResponse:
    """Route users to the right home after login, by role."""
    user = cast(UserType, request.user)
    if user.is_tenant:
        return redirect("core:portal")
    return redirect("core:dashboard")
