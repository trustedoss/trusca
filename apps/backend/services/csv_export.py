# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Shared pieces for streaming a table to CSV (B5).

The audit log has exported CSV since PR #14, and three more tables need the
same thing. The parts worth having in one place are the ones where a second
copy would be a security bug rather than a style problem:

  - Formula-injection escaping. A cell beginning ``=``, ``+``, ``-``, ``@``,
    or a tab/CR is a live formula the moment a spreadsheet opens the file
    (CWE-1236). Three copies of that prefix tuple is three chances for one
    of them to fall behind.
  - The byte-order mark. Excel on a CJK locale decodes a BOM-less UTF-8 file
    as CP949 or Shift-JIS and renders every Korean row as mojibake.
  - Quoting. Commas, embedded quotes and newlines are the stdlib's job, and
    the stdlib is only correct if every caller reaches it the same way.

What is deliberately NOT here: how a table decides which rows a caller may
see. Each export answers that by calling its own list service rather than
rebuilding the query, so the export cannot drift away from the access check
the list already passed.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime
from typing import Any

__all__ = [
    "CSV_BOM",
    "CSV_MEDIA_TYPE",
    "CSV_STREAM_CHUNK_ROWS",
    "ExportTooLarge",
    "csv_cell",
    "csv_line",
]

#: Prepended to the header row. Excel needs it to read UTF-8; every other
#: consumer treats it as a zero-width no-break space and ignores it.
CSV_BOM = "﻿"

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"

#: Rows asked for per round trip while streaming.
#:
#: What is actually fetched is smaller: every list service clamps its own
#: `limit` (500 for findings and components, 200 for the inventory), so a
#: 100k export is two hundred round trips in the first two cases and five
#: hundred in the third, not a hundred. The number is a ceiling on the ask
#: rather than a promise about the page, and the export's rate limit is set
#: against the real count rather than this one.
CSV_STREAM_CHUNK_ROWS = 1_000

#: Leading characters a spreadsheet treats as the start of a formula.
#: ``-`` and ``@`` are here for the same reason as ``=``: Excel and LibreOffice
#: both accept them as formula introducers.
_DANGEROUS_CSV_PREFIX = ("=", "+", "-", "@", "\t", "\r")


class ExportTooLarge(Exception):
    """
    The filtered result set is larger than an export is willing to build.

    Raised before the first byte is yielded, so the caller can turn it into a
    413 rather than a truncated file the reader would trust. A partial CSV is
    worse than a refused one: nothing in the file says it is partial.
    """

    status_code = 413
    title = "Export Too Large"

    def __init__(self, message: str, *, type_uri: str, extension: str) -> None:
        super().__init__(message)
        self.type_uri = type_uri
        self.extension = extension

    @property
    def extensions(self) -> dict[str, object]:
        """RFC 7807 members the SPA keys its message off."""
        return {self.extension: True}


def csv_cell(value: Any) -> str:
    """
    Render one value, neutralising anything a spreadsheet would execute.

    The apostrophe is the OWASP-recommended guard: spreadsheets read it as
    "the rest of this cell is text" and do not display it, so the cell reads
    as written while the formula never runs.
    """
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bool):
        # Before the general str() branch: Python renders these as "True" /
        # "False", and a CSV reader expects the lowercase JSON spelling.
        return "true" if value else "false"
    rendered = str(value)
    if rendered and rendered[0] in _DANGEROUS_CSV_PREFIX:
        return "'" + rendered
    return rendered


def csv_line(values: tuple[Any, ...] | list[Any]) -> str:
    """Render a single CSV row, including the trailing newline."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow([csv_cell(v) for v in values])
    return buffer.getvalue()
