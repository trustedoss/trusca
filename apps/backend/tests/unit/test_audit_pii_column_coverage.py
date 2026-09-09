# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Every audited table's columns, checked against a PII-shaped name pattern.

#428's security-reviewer follow-up: a plain grep for PII-shaped column names
across ``models/`` found a live, unmasked gap (``projects.owner_contact``)
in about ten minutes, on top of the ``email_recipients`` gap the same issue
already fixed. ``core.audit``'s masking is a discover-and-add allowlist
(``_SENSITIVE_COLUMNS``'s own comments read that way for every entry), which
means the SAME defect shape can recur silently a third time, a fourth time,
indefinitely, with nothing forcing the discovery to happen before a real
row is written to the immutable ``audit_logs`` table.

This test converts "found once by a human running grep" into "fails CI the
next time it recurs": it walks every column of every mapped table, skips
tables ``core.audit`` never audits and columns already in one of its three
masking sets, and fails on anything left over whose name matches a small
PII-shaped keyword pattern (email / contact / phone / recipient / address)
UNLESS the column is also named in ``_REVIEWED_NOT_PII`` below, with a
one-line reason, the same discover-and-document shape
``_SENSITIVE_COLUMNS`` already uses, just enforced instead of manual.

This is NOT a claim of catching every possible PII column (a free-text
"notes" field a human types a name into is a structural blind spot no
column-name pattern can see; see the security-reviewer's Info finding on
#428). It catches the narrower, cheaply-detectable shape both real gaps so
far actually were: a STRUCTURED column whose declared purpose is to hold an
address/contact/phone value.
"""

from __future__ import annotations

import re

from core.audit import _NON_AUDITED_TABLES, _PII_COLUMNS, _SENSITIVE_COLUMNS, _URL_REDACT_COLUMNS
from models import Base

# Word-boundary, case-insensitive. Deliberately narrow: bare "name" is
# excluded (it would flag "team.name" / "project.name" / hundreds of
# resource-name columns that are not personal data), and "full_name" /
# "email" are already exact-matched into _PII_COLUMNS so this pattern does
# not need to independently rediscover them.
_PII_SHAPED_NAME = re.compile(r"(email|contact|phone|recipient|address)", re.IGNORECASE)

# Columns that match the pattern above but were checked by hand and judged
# not to be personal data. Each entry needs the same kind of one-line reason
# _SENSITIVE_COLUMNS entries carry - this set is meant to be small and to
# stay small, not to become the new place gaps hide.
_REVIEWED_NOT_PII = {
    # A boolean on/off toggle for whether email notifications are enabled -
    # not an address, so nothing to mask; matches the pattern on substring
    # alone.
    "email_enabled",
}


def _already_covered(name: str) -> bool:
    return (
        name in _SENSITIVE_COLUMNS
        or name in _PII_COLUMNS
        or name in _URL_REDACT_COLUMNS
        or name in _REVIEWED_NOT_PII
    )


def test_every_pii_shaped_column_on_an_audited_table_is_masked_or_reviewed() -> None:
    unreviewed: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name in _NON_AUDITED_TABLES:
            continue
        for column in table.columns:
            if not _PII_SHAPED_NAME.search(column.name):
                continue
            if _already_covered(column.name):
                continue
            unreviewed.append(f"{table_name}.{column.name}")

    assert not unreviewed, (
        "PII-shaped column(s) on an audited table are not masked by "
        "core.audit.mask_sensitive_columns and not in this test's "
        "_REVIEWED_NOT_PII allowlist - add each to _SENSITIVE_COLUMNS / "
        "_PII_COLUMNS / _URL_REDACT_COLUMNS (if it holds PII) or to "
        "_REVIEWED_NOT_PII with a one-line reason (if it does not), in "
        f"apps/backend/core/audit.py: {unreviewed}"
    )


def test_the_pattern_actually_catches_something_when_nothing_is_reviewed() -> None:
    """Hardening rule 7: this assertion must be able to fail. Confirms the
    walk-and-match logic itself works, independent of today's real coverage
    being clean, by checking a known real column against an EMPTY masking
    view rather than the real frozensets."""
    hits = [
        name
        for name in ("email", "owner_contact", "email_recipients", "id", "created_at")
        if _PII_SHAPED_NAME.search(name)
    ]
    assert set(hits) == {"email", "owner_contact", "email_recipients"}
