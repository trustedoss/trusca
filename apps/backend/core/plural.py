# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""English singular/plural helpers for server-built text.

Messages the API returns or writes into a file ("7 scans queued or running",
"1 more component omitted") are read by people, so they pick the right noun
form instead of writing "scan(s)". The localized UI renders its own text from
``detail_code``/``detail_params`` where one exists; these helpers cover the
English fallback and the text that has no client-side rendering.
"""

from __future__ import annotations


def noun(count: int, singular: str, plural: str | None = None) -> str:
    """Return ``singular`` when ``count`` is exactly 1, else the plural form.

    The plural defaults to ``singular + "s"``; pass ``plural`` for nouns that
    do not follow that rule ("entry" / "entries").
    """
    if count == 1:
        return singular
    return plural if plural is not None else f"{singular}s"


def count_noun(count: int, singular: str, plural: str | None = None) -> str:
    """Return ``"<count> <noun>"`` with the noun in the matching form."""
    return f"{count} {noun(count, singular, plural)}"
