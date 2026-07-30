"""Custom user model.

Replaces the legacy `users` table (id, name, sname, email, password[md5],
created, fore_start). Auth uses email as the login identifier; legacy MD5
password hashes are re-hashed to Argon2 on first successful login (handled in
the auth backend, added in a later phase).
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    # Legacy `fore_start`: day-of-month from which income is forecast.
    forecast_start_day = models.PositiveSmallIntegerField(default=20)

    class Meta:
        db_table = "accounts_user"

    def __str__(self) -> str:
        return self.get_username()
