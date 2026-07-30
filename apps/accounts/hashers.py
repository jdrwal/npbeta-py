"""Password hasher for legacy MD5 hashes imported from the old PHP app.

The legacy `users.password` column stores unsalted ``md5(password)``. Imported
users get their password stored as ``legacy_md5$<hexdigest>``. This hasher can
verify such passwords; ``must_update`` returns True so Django transparently
re-hashes to the preferred hasher (Argon2) on the user's first successful login.
"""

import hashlib
from typing import Any

from django.contrib.auth.hashers import BasePasswordHasher
from django.utils.crypto import constant_time_compare


class LegacyMD5PasswordHasher(BasePasswordHasher):
    algorithm = "legacy_md5"

    def salt(self) -> str:
        return ""

    def encode(self, password: str, salt: str) -> str:
        digest = hashlib.md5(password.encode()).hexdigest()  # noqa: S324 (legacy compat)
        return f"{self.algorithm}${digest}"

    def verify(self, password: str, encoded: str) -> bool:
        _, digest = encoded.split("$", 1)
        return constant_time_compare(
            digest, hashlib.md5(password.encode()).hexdigest()  # noqa: S324
        )

    def must_update(self, encoded: str) -> bool:
        # Always upgrade legacy hashes to the preferred hasher on next login.
        return True

    def safe_summary(self, encoded: str) -> dict[Any, Any]:
        _, digest = encoded.split("$", 1)
        return {"algorithm": self.algorithm, "hash": digest[:6] + "..."}

    def harden_runtime(self, password: str, encoded: str) -> None:
        pass
