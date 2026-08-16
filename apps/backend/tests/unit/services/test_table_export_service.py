# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The three table exports (B5).

These drive the streaming machinery through a stub list service rather than
a database. What is under test here is the part that is the export's own:
the row cap, the column contract, and the page walk. Whether the rows are
the right rows is the list service's question, and the export answers it by
calling that service rather than by having an opinion.
"""

from __future__ import annotations

from typing import Any

import pytest

from services import table_export_service as svc
from services.csv_export import ExportTooLarge


async def _drain(stream: Any) -> str:
    return "".join([chunk async for chunk in stream])


async def _drain_capturing_logs(stream: Any, sink: list[dict[str, Any]]) -> str:
    """Drain a stream with the module's logger swapped for a recorder.

    The service binds ``log`` at import time, so reconfiguring structlog
    afterwards never reaches it; replacing the attribute does.
    """

    class _Recorder:
        def warning(self, event: str, **fields: Any) -> None:
            sink.append({"event": event, **fields})

        def info(self, event: str, **fields: Any) -> None:
            sink.append({"event": event, **fields})

    original = svc.log
    svc.log = _Recorder()  # type: ignore[assignment]
    try:
        return await _drain(stream)
    finally:
        svc.log = original  # type: ignore[assignment]


def _pager(rows: list[dict[str, Any]], total: int | None = None):
    """A stub list service that pages an in-memory list."""
    calls: list[tuple[int, int]] = []

    async def fetch_page(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        calls.append((limit, offset))
        return rows[offset : offset + limit], total if total is not None else len(rows)

    return fetch_page, calls


class _Boom(ExportTooLarge):
    def __init__(self, message: str) -> None:
        super().__init__(message, type_uri="urn:test", extension="boom")


@pytest.mark.asyncio
async def test_stream_leads_with_the_bom_and_the_header() -> None:
    fetch_page, _calls = _pager([{"a": 1, "b": 2}])
    body = await _drain(
        svc._stream(
            columns=("a", "b"),
            remap={},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        )
    )
    assert body.startswith("﻿a,b\n")
    assert "1,2\n" in body


@pytest.mark.asyncio
async def test_stream_refuses_before_writing_anything() -> None:
    """
    The cap is answered with an exception, not a short file.

    A truncated CSV says nothing about being truncated, and the reader who
    opens it has no way to know they are looking at part of the answer.
    """
    fetch_page, _calls = _pager([{"a": 1}], total=svc.EXPORT_HARD_LIMIT + 1)
    stream = svc._stream(
        columns=("a",),
        remap={},
        fetch_page=fetch_page,
        too_large=_Boom,
        label="test",
    )
    with pytest.raises(_Boom):
        await stream.__anext__()


@pytest.mark.asyncio
async def test_stream_walks_every_page() -> None:
    rows = [{"a": n} for n in range(2500)]
    fetch_page, calls = _pager(rows)
    body = await _drain(
        svc._stream(
            columns=("a",),
            remap={},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        )
    )
    # Header, every row, and the trailer. No row seen twice.
    assert len(body.strip().split("\n")) == 2502
    assert body.endswith("# rows: 2500\n")
    assert calls == [(1000, 0), (1000, 1000), (1000, 2000)]


@pytest.mark.asyncio
async def test_stream_stops_when_the_result_set_shrinks_underneath_it() -> None:
    """
    A rescan can replace the snapshot mid-export. Stop rather than spin.

    The count came from the first page; if a later page comes back empty the
    rows it promised are gone, and asking again would loop forever.
    """
    calls: list[int] = []

    async def fetch_page(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        calls.append(offset)
        if offset == 0:
            return [{"a": 1}], 5000
        return [], 5000

    body = await _drain(
        svc._stream(
            columns=("a",),
            remap={},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        )
    )
    # One row, then the trailer that says one row: the file's own count is
    # what tells a reader it holds less than the list promised.
    assert body == "﻿a\n1\n# rows: 1\n"
    assert calls == [0, 1]


@pytest.mark.asyncio
async def test_stream_renders_a_missing_field_as_empty() -> None:
    # A list payload that grows a field should not take the export down.
    fetch_page, _calls = _pager([{"a": 1}])
    body = await _drain(
        svc._stream(
            columns=("a", "not_there"),
            remap={},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        )
    )
    assert "1,\n" in body


@pytest.mark.asyncio
async def test_stream_escapes_a_formula_coming_from_the_data() -> None:
    fetch_page, _calls = _pager([{"name": "=HYPERLINK(\"http://evil.example\")"}])
    body = await _drain(
        svc._stream(
            columns=("name",),
            remap={},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        )
    )
    assert "'=HYPERLINK" in body
    assert "=HYPERLINK" not in body.replace("'=HYPERLINK", "")


@pytest.mark.asyncio
async def test_remap_reaches_a_differently_named_key() -> None:
    fetch_page, _calls = _pager([{"id": "abc"}])
    body = await _drain(
        svc._stream(
            columns=("finding_id",),
            remap={"finding_id": "id"},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        )
    )
    assert "abc\n" in body


def test_each_export_declares_its_own_problem_type() -> None:
    """
    Three distinct URNs and extensions, so the SPA can say which table.

    One shared "export too large" would leave the message unable to name the
    filter the reader needs to narrow.
    """
    errors = [
        svc.VulnerabilitiesExportTooLarge("x"),
        svc.ComponentsExportTooLarge("x"),
        svc.InventoryExportTooLarge("x"),
    ]
    assert len({e.type_uri for e in errors}) == 3
    assert len({e.extension for e in errors}) == 3
    for err in errors:
        assert err.status_code == 413
        assert err.extensions == {err.extension: True}


def test_column_contracts_are_stable_and_unique() -> None:
    # The order is the contract: a reader with a pivot table on last month's
    # export should not find the columns shuffled in this month's.
    for columns in (
        svc.VULNERABILITIES_CSV_COLUMNS,
        svc.COMPONENTS_CSV_COLUMNS,
        svc.INVENTORY_CSV_COLUMNS,
    ):
        assert len(columns) == len(set(columns))
        assert all(c and c.islower() for c in columns)


@pytest.mark.asyncio
async def test_stream_ends_with_a_row_count(monkeypatch) -> None:
    """
    A short file can be told from a complete one.

    The status and headers are committed before the second page is fetched,
    so anything failing after that truncates the body inside a 200. Nothing
    in a CSV says it stopped early, and a partial export attached to a
    customer deliverable understates risk.
    """
    fetch_page, _calls = _pager([{"a": 1}, {"a": 2}])
    body = await _drain(
        svc._stream(
            columns=("a",),
            remap={},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        )
    )
    assert body.endswith("# rows: 2\n")


@pytest.mark.asyncio
async def test_stream_says_so_when_it_stops_short() -> None:
    """The trailer disagrees with the promised total, and the log says why."""
    logged: list[dict[str, Any]] = []

    async def fetch_page(limit: int, offset: int):
        if offset == 0:
            return [{"a": 1}], 5000
        return [], 5000

    body = await _drain_capturing_logs(
        svc._stream(
            columns=("a",),
            remap={},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        ),
        logged,
    )

    # One row written against a promised five thousand: the count is the
    # only thing in the file that says so.
    assert body.endswith("# rows: 1\n")
    # And the operator gets told, because a reader who never compares the
    # trailer against their own row count would otherwise never know.
    truncations = [e for e in logged if e.get("event") == "export.csv_truncated"]
    assert truncations, f"no truncation warning was logged: {logged}"
    assert truncations[0]["written"] == 1
    assert truncations[0]["expected_at_start"] == 5000


@pytest.mark.asyncio
async def test_stream_notices_a_result_set_that_grew_underneath_it() -> None:
    """
    Growing counts as short too, and looks complete without this.

    The walk stops at the first page's total, so a set that grew mid-export
    ends with ``offset == total`` and a trailer that matches. Only the last
    page's count says rows were left behind.
    """
    logged: list[dict[str, Any]] = []

    async def fetch_page(limit: int, offset: int):
        # Two rows promised at the start, four by the time we get there.
        return ([{"a": offset}] if offset < 2 else []), (2 if offset == 0 else 4)

    await _drain_capturing_logs(
        svc._stream(
            columns=("a",),
            remap={},
            fetch_page=fetch_page,
            too_large=_Boom,
            label="test",
        ),
        logged,
    )

    truncations = [e for e in logged if e.get("event") == "export.csv_truncated"]
    assert truncations, f"a grown result set went unreported: {logged}"
    assert truncations[0]["expected_at_end"] == 4


# ---------------------------------------------------------------------------
# CSV export columns vs the keys their list services actually return
# ---------------------------------------------------------------------------


def test_export_columns_resolve_against_their_list_payloads() -> None:
    """Every export column names a key its list service really returns.

    This is the defect class that actually happened while B5 was written: all
    three column tuples were first written against guessed field names, and
    two of the three were wrong. `_row` renders a key it cannot find as an
    empty cell rather than raising, so the failure is silent: the file gets
    a column of blanks and every existing test still passes, because the
    integration tests compare the header line (a hardcoded copy of the same
    guess) and almost never look at a value.

    The keys are read out of the service source rather than by calling it:
    the projection is a dict literal in one place per service, and a unit
    test that needed a database would not run here.
    """
    import ast
    import pathlib

    from services import table_export_service as svc

    backend = pathlib.Path(__file__).resolve().parents[3]

    def dict_keys_in(path: str, function: str) -> set[str]:
        """Every string key of every dict literal built inside `function`."""
        tree = ast.parse((backend / path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
                and node.name == function
            ):
                return {
                    key.value
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Dict)
                    for key in inner.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
        raise AssertionError(f"{function} not found in {path}")

    def model_fields(module: str, name: str) -> set[str]:
        import importlib

        return set(getattr(importlib.import_module(module), name).model_fields)

    cases = [
        (
            "vulnerabilities",
            svc.VULNERABILITIES_CSV_COLUMNS,
            {
                "component_name": "affected_component_name",
                "component_version": "affected_component_version",
                "component_license": "affected_component_license",
                "finding_id": "id",
            },
            dict_keys_in(
                "services/vulnerability_service.py", "list_project_vulnerabilities"
            ),
        ),
        (
            "components",
            svc.COMPONENTS_CSV_COLUMNS,
            {},
            dict_keys_in(
                "services/project_detail_service.py", "list_components_for_project"
            ),
        ),
        (
            "inventory",
            svc.INVENTORY_CSV_COLUMNS,
            {},
            model_fields("schemas.inventory", "InventoryComponentRow"),
        ),
    ]

    for label, columns, remap, available in cases:
        wanted = {remap.get(column, column) for column in columns}
        missing = wanted - available
        assert not missing, (
            f"{label} export names columns its list service does not return: "
            f"{sorted(missing)}, so those cells would be silently empty"
        )


def test_export_remaps_match_the_service_they_rename_from() -> None:
    """The remap tables in the stream functions are the ones asserted above.

    Kept as its own check so the pairing cannot drift: if someone adds a
    remap entry in `stream_vulnerabilities_csv` the test above stops covering
    the column it renames, and would go on passing.
    """
    import inspect

    from services import table_export_service as svc

    source = inspect.getsource(svc.stream_vulnerabilities_csv)
    for csv_name, service_key in (
        ("component_name", "affected_component_name"),
        ("component_version", "affected_component_version"),
        ("component_license", "affected_component_license"),
        ("finding_id", "id"),
    ):
        assert f'"{csv_name}": "{service_key}"' in source, (
            f"the vulnerabilities export no longer remaps {csv_name} to "
            f"{service_key}; the column contract test needs updating with it"
        )
