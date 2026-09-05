# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Moving stored ciphertext onto the newest encryption key.

The dangerous half of a rotation is not the re-encryption, it is knowing when
it is finished. An operator who removes the old key while rows still hold
ciphertext written under it has made those rows permanently unreadable, and
nothing about the running system looks wrong until something tries to read
one.

So counting what is left and rewriting what is left go through the same
predicate, :func:`is_on_current_key`. Not two implementations kept in
agreement: one implementation. Two would be a pair that can drift, and the
cost of that drift here is a report of zero while rows remain.

How a row is judged
-------------------
A Fernet token carries a version byte and a timestamp and nothing that names
the key that produced it. Which key wrote a row can only be answered by
trying to open it. Attempting one key over ten thousand rows costs under a
tenth of a second (one HMAC each), so the scan is a full one rather than a
sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import configured_keys, purpose_cipher
from core.encrypted_columns import ENCRYPTED_COLUMNS, EncryptedColumn

log = structlog.get_logger("key_rotation")

#: Rows read per statement. Large enough that the round trips do not dominate,
#: small enough that a table nobody expected to be large does not arrive in
#: one list.
_BATCH = 500


def is_on_current_key(ciphertext: str, *, purpose: str | None) -> bool:
    """Whether ``ciphertext`` opens with the key new writes use.

    False means the row was written under an older key and still needs
    re-encrypting. It also means the row cannot be read at all, if the key
    that wrote it has already been removed; the two are indistinguishable
    from here, which is why this is used to refuse a removal rather than to
    diagnose one.
    """
    newest = Fernet(configured_keys()[0])
    if purpose is not None:
        newest = purpose_cipher(purpose, configured_keys()[0])
    try:
        newest.decrypt(ciphertext.encode("utf-8"))
    except (InvalidToken, ValueError, TypeError):
        return False
    return True


@dataclass
class ColumnProgress:
    """What one column looked like after a pass over it."""

    column: EncryptedColumn
    scanned: int = 0
    stale: int = 0
    rewritten: int = 0
    #: Rows that were stale when read and had changed by the time the update
    #: ran. Not a failure: somebody wrote the row while the pass was going,
    #: and an application write already uses the newest key.
    raced: int = 0
    unreadable: int = 0

    @property
    def remaining(self) -> int:
        """Stale rows this pass did not move, which is what blocks removal."""
        return self.unreadable + self.raced


@dataclass
class RotationReport:
    """The whole run, in the shape the caller prints."""

    columns: list[ColumnProgress] = field(default_factory=list)

    @property
    def stale_total(self) -> int:
        return sum(c.stale for c in self.columns)

    @property
    def rewritten_total(self) -> int:
        return sum(c.rewritten for c in self.columns)

    @property
    def unreadable_total(self) -> int:
        return sum(c.unreadable for c in self.columns)


async def count_stale(session: AsyncSession) -> RotationReport:
    """How many rows are still on an older key, without changing anything.

    This is the question an operator has to answer before removing a key, and
    it is answered by the same predicate that decides what to rewrite.

    Stale rows are separated into two kinds, because they need opposite
    actions. A row an older key still opens is work the rewrite will do. A row
    no configured key opens is a key that has already been removed too early,
    and running the rewrite will not help: the key has to come back first.
    Reporting them as one number would tell an operator to run a command that
    cannot fix what they have.

    Only stale rows are opened, so a deployment with nothing outstanding pays
    one failed HMAC per row and no decryption at all.
    """
    from core.crypto import decrypt_secret

    report = RotationReport()
    for column in ENCRYPTED_COLUMNS:
        progress = ColumnProgress(column=column)
        rows = await _read_rows(session, column)
        for _pk, value in rows:
            progress.scanned += 1
            if is_on_current_key(value, purpose=column.purpose):
                continue
            progress.stale += 1
            try:
                decrypt_secret(value, purpose=column.purpose)
            except Exception:  # noqa: BLE001 - any failure means the same thing
                progress.unreadable += 1
        report.columns.append(progress)
    return report


async def reencrypt(session: AsyncSession) -> RotationReport:
    """Rewrite every row that is not on the newest key.

    Safe to interrupt and safe to repeat. A row already on the newest key is
    not touched, so a second run over a finished column does nothing.

    The update is conditional on the value that was read. A row somebody
    changed in between updates zero rows and is left alone, because an
    application write already used the newest key and rewriting it from the
    value read earlier would put back what they replaced.
    """
    from core.crypto import decrypt_secret, encrypt_secret

    report = RotationReport()
    for column in ENCRYPTED_COLUMNS:
        progress = ColumnProgress(column=column)
        rows = await _read_rows(session, column)

        for pk, value in rows:
            progress.scanned += 1
            if is_on_current_key(value, purpose=column.purpose):
                continue
            progress.stale += 1

            try:
                plaintext = decrypt_secret(value, purpose=column.purpose)
            except Exception:  # noqa: BLE001 - any failure here means the same thing
                # No key in the list opens this row. Counted, never fatal: one
                # unreadable row must not stop the other columns from being
                # moved, and the count is what tells the operator that a key
                # they still need is missing.
                progress.unreadable += 1
                log.error(
                    "key_rotation.unreadable_row",
                    column=column.label,
                    row_id=str(pk),
                    detail=(
                        "no configured key opens this row; a key it was "
                        "written under is missing from GITHUB_APP_ENCRYPTION_KEY"
                    ),
                )
                continue

            # RETURNING rather than rowcount: the value comes back as rows,
            # which is typed, and an empty result says the same thing a zero
            # rowcount would without depending on the driver reporting it.
            # The table and column names are interpolated because a bind
            # parameter cannot stand where an identifier goes. They come from
            # ``ENCRYPTED_COLUMNS``, which holds literals written in that
            # module and rejects anything that is not a plain identifier when
            # it is constructed, so no caller-supplied value reaches here. The
            # ciphertext, the key and the old value are all bound.
            result = await session.execute(
                # nosemgrep: avoid-sqlalchemy-text
                text(
                    f"UPDATE {column.table} SET {column.column} = :new "  # noqa: S608
                    f" WHERE {column.primary_key} = :pk AND {column.column} = :old"
                    f" RETURNING {column.primary_key}"
                ),
                {
                    "new": encrypt_secret(plaintext, purpose=column.purpose),
                    "pk": pk,
                    "old": value,
                },
            )
            if result.fetchall():
                progress.rewritten += 1
            else:
                # The row changed between the read and here. An application
                # write already used the newest key, so it needs nothing.
                progress.raced += 1

        await session.commit()
        log.info(
            "key_rotation.column_done",
            column=column.label,
            scanned=progress.scanned,
            stale=progress.stale,
            rewritten=progress.rewritten,
            raced=progress.raced,
            unreadable=progress.unreadable,
        )
        report.columns.append(progress)

    return report


async def _read_rows(
    session: AsyncSession, column: EncryptedColumn
) -> list[tuple[object, str]]:
    """Every non-null ciphertext in one column, with its primary key.

    Ordered by primary key so a run that is interrupted and restarted covers
    the same rows in the same order, which makes a partial run's progress
    mean something.
    """
    rows: list[tuple[object, str]] = []
    offset = 0
    while True:
        # Identifiers interpolated, everything else bound. Same reasoning as
        # the update above: ``EncryptedColumn`` refuses a name that is not a
        # plain identifier at construction, and every instance is a literal.
        result = await session.execute(
            # nosemgrep: avoid-sqlalchemy-text
            text(
                f"SELECT {column.primary_key} AS pk, {column.column} AS value "  # noqa: S608
                f"  FROM {column.table} "
                f" WHERE {column.column} IS NOT NULL "
                f" ORDER BY {column.primary_key} "
                f" LIMIT :limit OFFSET :offset"
            ),
            {"limit": _BATCH, "offset": offset},
        )
        batch = result.fetchall()
        if not batch:
            return rows
        rows.extend((row.pk, row.value) for row in batch)
        offset += len(batch)


__all__ = [
    "ColumnProgress",
    "RotationReport",
    "count_stale",
    "is_on_current_key",
    "reencrypt",
]
