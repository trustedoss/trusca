# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Copyright backfill from ClearlyDefined attributions — unit tests (S5-A).

The NOTICE has always had a slot for per-component copyright holders and has
been printing "holders not captured in SBOM — see source" whenever the SBOM
carried none. ClearlyDefined harvests exactly those holders, so they go into
the same ``scan_components.raw_data["copyright"]`` field the SBOM would have
used — which is why the NOTICE query and all three renderers are untouched by
this phase.

Two rules the tests exist to hold: never overwrite what cdxgen extracted from
the package itself, and never store more than the NOTICE column will read.
"""

from __future__ import annotations

import uuid
from typing import Any

from tasks.scan_source import _backfill_component_copyright


class _FakeRow:
    def __init__(self, raw_data: dict[str, Any] | None) -> None:
        # Typed loosely on purpose: the production column is nullable JSONB and
        # the tests read it back as a dict after the backfill has run.
        self.raw_data: Any = raw_data


class _FakeResult:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[_FakeRow]:
        return self._rows


class _FakeSession:
    """Just enough Session to run the backfill — no database involved."""

    def __init__(self, rows: list[_FakeRow]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> _FakeResult:
        self.statements.append(statement)
        return _FakeResult(self.rows)


def _run(rows: list[_FakeRow], attributions: list[str]) -> _FakeSession:
    session = _FakeSession(rows)
    _backfill_component_copyright(
        session,  # type: ignore[arg-type]
        scan_uuid=uuid.uuid4(),
        component_version_id=uuid.uuid4(),
        attributions=attributions,
    )
    return session


def test_fills_an_empty_copyright() -> None:
    row = _FakeRow({"path": "node_modules/lodash"})
    _run([row], ["Copyright OpenJS Foundation", "Copyright Jeremy Ashkenas"])

    assert row.raw_data["copyright"] == (
        "Copyright OpenJS Foundation; Copyright Jeremy Ashkenas"
    )
    assert row.raw_data["copyright_source"] == "clearlydefined"
    # Untouched keys survive — this edits the blob, it does not replace it.
    assert row.raw_data["path"] == "node_modules/lodash"


def test_never_overwrites_what_the_sbom_captured() -> None:
    """cdxgen read the package itself and is the better source when it answered."""
    row = _FakeRow({"copyright": "Copyright 2019 The Authors"})
    _run([row], ["Copyright Someone Else"])

    assert row.raw_data["copyright"] == "Copyright 2019 The Authors"
    assert "copyright_source" not in row.raw_data


def test_a_whitespace_only_copyright_counts_as_empty() -> None:
    row = _FakeRow({"copyright": "   "})
    _run([row], ["Copyright Real Holder"])
    assert row.raw_data["copyright"] == "Copyright Real Holder"


def test_fills_every_row_for_the_component() -> None:
    """A component version can appear at several dependency paths."""
    rows = [_FakeRow({"path": "a"}), _FakeRow({"path": "b"})]
    _run(rows, ["Copyright Holder"])
    assert all(row.raw_data["copyright"] == "Copyright Holder" for row in rows)


def test_a_row_with_its_own_copyright_is_skipped_while_its_sibling_is_filled() -> None:
    already = _FakeRow({"copyright": "Copyright From SBOM"})
    empty = _FakeRow({})
    _run([already, empty], ["Copyright From ClearlyDefined"])

    assert already.raw_data["copyright"] == "Copyright From SBOM"
    assert empty.raw_data["copyright"] == "Copyright From ClearlyDefined"


def test_clamped_to_what_the_notice_column_reads() -> None:
    """The NOTICE query reads left(…, 500); storing more only truncates later."""
    row = _FakeRow({})
    _run([row], [f"Copyright Holder Number {index}" for index in range(200)])
    assert len(row.raw_data["copyright"]) == 500


def test_no_attributions_is_a_no_op() -> None:
    row = _FakeRow({"path": "x"})
    session = _run([row], [])
    assert row.raw_data == {"path": "x"}
    # And it does not even query — nothing to write.
    assert session.statements == []


def test_a_null_raw_data_becomes_a_dict() -> None:
    row = _FakeRow(None)
    _run([row], ["Copyright Holder"])
    assert row.raw_data["copyright"] == "Copyright Holder"
