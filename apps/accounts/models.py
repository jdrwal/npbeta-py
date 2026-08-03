"""Custom user model.

Replaces the legacy `users` table (id, name, sname, email, password[md5],
created, fore_start). Auth uses email as the login identifier; legacy MD5
password hashes are re-hashed to Argon2 on first successful login (handled in
the auth backend, added in a later phase).
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    # Legacy `fore_start`: day-of-month from which income is forecast.
    forecast_start_day = models.PositiveSmallIntegerField(default=20)
    # Inactivity logout window in minutes (5 min .. 6 h), enforced per request.
    session_timeout_minutes = models.PositiveSmallIntegerField(default=30)

    class Role(models.TextChoices):
        LANDLORD = "landlord", "Wynajmujący"
        TENANT = "tenant", "Najemca"

    # Open platform: an account is either a landlord (manages properties) or a
    # tenant (sees only their own contracts). Existing accounts default to landlord.
    role = models.CharField(
        max_length=16, choices=Role.choices, default=Role.LANDLORD
    )
    # Set once the account confirms its email via the activation link.
    email_verified = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_user"

    def __str__(self) -> str:
        return self.get_username()

    @property
    def is_landlord(self) -> bool:
        return self.role == self.Role.LANDLORD

    @property
    def is_tenant(self) -> bool:
        return self.role == self.Role.TENANT


class MailSettings(models.Model):
    """Per-user outgoing SMTP configuration used for tenant mailings."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mail_settings",
    )
    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_user = models.CharField(max_length=255, blank=True)
    smtp_password = models.CharField(max_length=255, blank=True)
    from_email = models.EmailField(max_length=255, blank=True)
    # Reply-To for outgoing mail; blank falls back to the account's own address.
    reply_to = models.EmailField(max_length=255, blank=True)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)
    # Test mode: redirect every outgoing mail to a single address so the
    # landlord can safely verify end-to-end delivery without mailing tenants.
    test_mode = models.BooleanField(default=False)
    # Where test-mode mail is redirected; blank falls back to the account address.
    test_recipient = models.EmailField(max_length=255, blank=True)
    # When True, fall back to the project's default mail backend instead of the
    # custom SMTP fields above.
    use_default = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "mail settings"

    def __str__(self) -> str:
        return f"Mail settings for {self.user}"

    @property
    def use_custom(self) -> bool:
        return not self.use_default and bool(self.smtp_host)
