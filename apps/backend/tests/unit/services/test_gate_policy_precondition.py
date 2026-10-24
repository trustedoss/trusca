# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The gate policy write precondition, without a database.

The HTTP behaviour is pinned in ``tests/integration/test_gate_policies_api.py``
and the ordering against the permission checks in
``tests/integration/test_existence_hide_state_matrix.py``. This file pins the
two pieces those rely on and cannot see directly: how a header value becomes a
set of tags, and that a tag changes when ``updated_at`` changes by the smallest
step the column can hold.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from api.v1.gate_policies import _entity_tags, _precondition
from models import GatePolicy
from services.gate_policy_service import (
    GatePolicyConflict,
    WritePrecondition,
    _check_precondition,
    gate_policy_etag,
)


def _row(updated_at: datetime) -> GatePolicy:
    return GatePolicy(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), team_id=None, updated_at=updated_at
    )


STAMP = datetime(2026, 10, 24, 9, 30, 15, 123456, tzinfo=UTC)


def test_the_tag_is_the_stamp_in_microseconds() -> None:
    assert gate_policy_etag(_row(STAMP)) == str(
        (STAMP - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    )


def test_one_microsecond_is_a_different_tag() -> None:
    """The column's resolution is the tag's: no two stored stamps share one."""
    later = STAMP + timedelta(microseconds=1)
    assert gate_policy_etag(_row(STAMP)) != gate_policy_etag(_row(later))


def test_the_same_instant_in_another_zone_is_the_same_tag() -> None:
    """asyncpg may hand the stamp back in any offset; the tag must not care."""
    seoul = STAMP.astimezone(timezone(timedelta(hours=9)))
    assert seoul.hour != STAMP.hour
    assert gate_policy_etag(_row(seoul)) == gate_policy_etag(_row(STAMP))


@pytest.mark.parametrize(
    ("raw", "tags"),
    [
        ('"123"', ("123",)),
        ('W/"123"', ("123",)),
        ('"1", "2"', ("1", "2")),
        ("*", ("*",)),
        ("", ()),
        ('""', ()),
        ("123", ("123",)),
    ],
)
def test_header_values_become_unquoted_tags(raw: str, tags: tuple[str, ...]) -> None:
    assert _entity_tags(raw) == tags


def test_no_headers_is_no_precondition() -> None:
    assert _precondition(None, None) == WritePrecondition()


def test_an_empty_if_match_is_still_a_precondition() -> None:
    """Present but empty must not collapse to "absent", or it would write blind."""
    assert _precondition("", None).if_match == ()


@pytest.mark.parametrize(("raw", "expected"), [("*", True), (" * ", True), ('"1"', False)])
def test_only_a_star_if_none_match_is_create_only(raw: str, expected: bool) -> None:
    assert _precondition(None, raw).if_none_match_any is expected


ROW = _row(STAMP)
CURRENT = gate_policy_etag(ROW)


@pytest.mark.parametrize(
    ("existing", "precondition"),
    [
        pytest.param(None, WritePrecondition(), id="no-header-no-row"),
        pytest.param(ROW, WritePrecondition(), id="no-header-row"),
        pytest.param(ROW, WritePrecondition(if_match=(CURRENT,)), id="current-tag"),
        pytest.param(ROW, WritePrecondition(if_match=("0", CURRENT)), id="tag-in-list"),
        pytest.param(ROW, WritePrecondition(if_match=("*",)), id="star-on-row"),
        pytest.param(None, WritePrecondition(if_none_match_any=True), id="create-only-no-row"),
    ],
)
def test_preconditions_that_hold(
    existing: GatePolicy | None, precondition: WritePrecondition
) -> None:
    _check_precondition(existing, precondition)


@pytest.mark.parametrize(
    ("existing", "precondition"),
    [
        pytest.param(ROW, WritePrecondition(if_match=("0",)), id="stale-tag"),
        pytest.param(ROW, WritePrecondition(if_match=()), id="empty-tag-list"),
        pytest.param(None, WritePrecondition(if_match=(CURRENT,)), id="tag-but-no-row"),
        pytest.param(None, WritePrecondition(if_match=("*",)), id="star-but-no-row"),
        pytest.param(ROW, WritePrecondition(if_none_match_any=True), id="create-only-on-row"),
    ],
)
def test_preconditions_that_fail(
    existing: GatePolicy | None, precondition: WritePrecondition
) -> None:
    with pytest.raises(GatePolicyConflict):
        _check_precondition(existing, precondition)
