# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The shared CSV pieces (B5).

These moved out of the audit service so three more tables could use them.
The tests moved with them: a second copy of the escaping is a security bug,
and a second copy of the tests is how the first copy stops being checked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from services.csv_export import (
    CSV_BOM,
    ExportTooLarge,
    csv_cell,
    csv_line,
)


@pytest.mark.parametrize(
    "payload",
    [
        '=cmd|"/c calc"!A1',
        "+1+1",
        "-2+3",
        "@SUM(1+1)",
        "\t=cmd",
        "\r=cmd",
        "=HYPERLINK(\"http://evil.example\",\"click\")",
    ],
)
def test_csv_cell_defuses_a_formula(payload: str) -> None:
    """
    A cell a spreadsheet would execute is prefixed with an apostrophe.

    Any of these opened in Excel or LibreOffice is a live formula, and the
    DDE variants reach the shell of whoever opened the file (CWE-1236). The
    apostrophe is not displayed, so the cell still reads as written.
    """
    rendered = csv_cell(payload)
    assert rendered.startswith("'")
    assert rendered[1:] == payload


@pytest.mark.parametrize(
    "payload",
    ["openssl", "1.2.3", "pkg:npm/lodash@4.17.21", "CVE-2026-1234", "critical"],
)
def test_csv_cell_leaves_ordinary_values_alone(payload: str) -> None:
    assert csv_cell(payload) == payload


def test_csv_cell_renders_the_types_a_row_actually_carries() -> None:
    assert csv_cell(None) == ""
    assert csv_cell(uuid.UUID(int=0)) == "00000000-0000-0000-0000-000000000000"
    assert csv_cell(datetime(2026, 8, 16, 9, 30, tzinfo=UTC)).startswith(
        "2026-08-16T09:30",
    )
    # A date is not a datetime; the EOL and SLA columns carry these.
    assert csv_cell(date(2026, 8, 16)) == "2026-08-16"


def test_csv_cell_renders_booleans_the_way_a_reader_expects() -> None:
    # Python spells these "True" / "False"; every other consumer of this file
    # spells them lowercase, and a column of mixed spellings is a sort key
    # that does not sort.
    assert csv_cell(True) == "true"
    assert csv_cell(False) == "false"


def test_csv_line_quotes_what_would_otherwise_break_the_row() -> None:
    rendered = csv_line(["a,b", 'c"d', "e\nf"])
    assert rendered == '"a,b","c""d","e\nf"\n'


def test_csv_line_escapes_every_cell_including_the_header() -> None:
    # The escaping lives inside `csv_line`, so there is no path from a value
    # to the output that skips it, header row included.
    assert csv_line(["=A1", "safe"]) == "'=A1,safe\n"


def test_csv_line_ends_rows_with_a_bare_newline() -> None:
    # Not CRLF. A stray CR is one of the prefixes `csv_cell` guards against,
    # and mixing the two makes the file's own line ending ambiguous.
    assert csv_line(["a"]).endswith("\n")
    assert "\r" not in csv_line(["a"])


def test_bom_is_the_zero_width_no_break_space() -> None:
    # Excel on a Korean locale reads a BOM-less UTF-8 file as CP949 and turns
    # every Korean row into mojibake.
    assert CSV_BOM == "﻿"


def test_export_too_large_carries_what_the_problem_body_needs() -> None:
    exc = ExportTooLarge(
        "too many",
        type_uri="https://example.invalid/errors/too-large",
        extension="widgets_export_too_large",
    )
    assert exc.status_code == 413
    assert exc.type_uri == "https://example.invalid/errors/too-large"
    # The SPA keys its message off the extension rather than off the prose.
    assert exc.extensions == {"widgets_export_too_large": True}
