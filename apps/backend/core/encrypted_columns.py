# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Every column holding ciphertext, and the key that opens it.

One list, because two lists disagree. Rotation reads it to know what to
re-encrypt, the audit reads it to count what is still on an older key, and a
contract test reads it to notice when a column moves between keys.

Why the list exists at all
--------------------------
A column's key can be changed by editing one call site, and nothing fails.
The service encrypts and decrypts with the same key, so its own tests pass;
what breaks is rows written before the change, and no test writes those.

That happened. A change moved ``registry_credentials.password_encrypted``
from the shared key to a derived subkey with no re-encryption, and the module
it edited carried a docstring forbidding exactly that outcome by a different
route. A sentence cannot fail. This list can.

Adding a column
---------------
Add the row here in the same change that adds the column. The contract test
fails otherwise, and the failure is the point: a column nobody listed is a
column rotation walks past, which is how a row ends up on a key the operator
has already removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: An unquoted SQL identifier. Table and column names reach a statement by
#: interpolation, because a placeholder cannot stand where an identifier goes,
#: so the values are constrained rather than trusted. Everything in the tuple
#: below is a literal written in this file, and this makes that a checked
#: property instead of a claim in a comment: a value that could carry a quote,
#: a semicolon or whitespace never reaches the string.
_IDENTIFIER = re.compile(r"\A[a-z_][a-z0-9_]*\Z")


@dataclass(frozen=True)
class EncryptedColumn:
    """One column of ciphertext.

    ``purpose`` is what gets passed to ``core.crypto`` when reading or
    writing it. ``None`` means the shared key. Changing this value for a
    column that already exists is a data migration, not an edit.
    """

    table: str
    column: str
    primary_key: str = "id"
    purpose: str | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("table", self.table),
            ("column", self.column),
            ("primary_key", self.primary_key),
        ):
            if not _IDENTIFIER.match(value):
                raise ValueError(
                    f"EncryptedColumn.{field}={value!r} is not a plain SQL "
                    "identifier. These are interpolated into statements, so "
                    "anything else is refused at import rather than reaching "
                    "the database."
                )

    @property
    def label(self) -> str:
        return f"{self.table}.{self.column}"


#: Newest last, so a reader sees the order they arrived in.
ENCRYPTED_COLUMNS: tuple[EncryptedColumn, ...] = (
    EncryptedColumn("github_app_credentials", "private_key_encrypted"),
    EncryptedColumn("github_app_credentials", "webhook_secret_encrypted"),
    EncryptedColumn("projects", "git_credential_encrypted"),
    EncryptedColumn("registry_credentials", "password_encrypted"),
    EncryptedColumn("projects", "webhook_secret_encrypted"),
    # The first column that is not on the shared key. A stolen TOTP secret
    # produces that person's second factor for ever, which is a different loss
    # from a stolen forge credential, and the column is new so nothing was
    # written under the shared key to migrate.
    EncryptedColumn("users", "mfa_secret_encrypted", purpose="totp"),
)


def columns_for_purpose(purpose: str | None) -> tuple[EncryptedColumn, ...]:
    """Every column read and written under one key.

    Rotation works a purpose at a time, because the cipher list is built per
    purpose: a subkey is derived from each master before the list is made.
    """
    return tuple(c for c in ENCRYPTED_COLUMNS if c.purpose == purpose)


def purposes() -> tuple[str | None, ...]:
    """The distinct keys in use, in the order the columns first name them."""
    seen: list[str | None] = []
    for column in ENCRYPTED_COLUMNS:
        if column.purpose not in seen:
            seen.append(column.purpose)
    return tuple(seen)


__all__ = ["ENCRYPTED_COLUMNS", "EncryptedColumn", "columns_for_purpose", "purposes"]
