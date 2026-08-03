"""Celery tasks for the accounts app (async account emails)."""

from __future__ import annotations

from celery import shared_task


@shared_task
def send_activation_email_task(user_id: int, activation_url: str) -> None:
    """Send the account activation email out of the request cycle.

    Runs on a worker so a slow or unreachable SMTP never blocks (or fails)
    registration. Silently returns if the user vanished before delivery.
    """
    from django.contrib.auth import get_user_model
    from django.core.mail import send_mail
    from django.template.loader import render_to_string

    from apps.core.services.mailer import with_footer

    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=user_id)
    except user_model.DoesNotExist:
        return
    body = render_to_string(
        "registration/activation_email.txt",
        {"user": user, "link": activation_url},
    )
    send_mail(
        subject="Aktywacja konta — Rozlicz Najem",
        message=with_footer(body),
        from_email=None,
        recipient_list=[user.email],
        fail_silently=False,
    )
