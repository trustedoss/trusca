"""
Unit tests for the pure helpers behind global search (H-2).

These exercise the two branches the integration suite doesn't naturally hit —
an all-whitespace / empty ``kinds`` CSV and the no-membership member scope —
plus the super-admin vs member choke-point branch in
:func:`core.authz.team_scope_filter`. No DB: the scope helper returns a
SQLAlchemy expression we compile to SQL and assert on.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import true

from core.authz import team_scope_filter
from core.security import CurrentUser
from services.search_service import ALLOWED_KINDS, MIN_QUERY_LEN, global_search, parse_kinds


def _member(team_ids: list[uuid.UUID]) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email="dev@example.com",
        role="developer",
        team_ids=team_ids,
        team_roles={tid: "developer" for tid in team_ids},
    )


def _super_admin() -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email="admin@example.com",
        role="super_admin",
        team_ids=[],
        team_roles={},
        is_superuser=True,
    )


# --- parse_kinds -----------------------------------------------------------


def test_parse_kinds_none_is_both() -> None:
    assert parse_kinds(None) == set(ALLOWED_KINDS)


def test_parse_kinds_empty_csv_is_both() -> None:
    # All-whitespace / bare commas collapse to no tokens → default to both.
    assert parse_kinds("  ") == set(ALLOWED_KINDS)
    assert parse_kinds(",") == set(ALLOWED_KINDS)


def test_parse_kinds_unknown_tokens_dropped() -> None:
    assert parse_kinds("components,bogus") == {"components"}
    # Only-unknown → empty set → caller returns empty results.
    assert parse_kinds("bogus,nope") == set()


def test_parse_kinds_case_insensitive() -> None:
    assert parse_kinds("Components,VULNERABILITIES") == set(ALLOWED_KINDS)


# --- team_scope_filter -----------------------------------------------------


def test_scope_super_admin_is_unrestricted() -> None:
    expr = team_scope_filter(_super_admin())
    # Compiles to the literal true predicate.
    assert str(expr.compile()) == str(true().compile())


def test_scope_member_with_no_teams_is_false() -> None:
    expr = team_scope_filter(_member([]))
    compiled = str(expr.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "false" in compiled


def test_scope_member_filters_by_team_in() -> None:
    tid = uuid.uuid4()
    expr = team_scope_filter(_member([tid]))
    compiled = str(expr.compile()).lower()
    assert "team_id in" in compiled or "team_id in (" in compiled


# --- MIN_QUERY_LEN floor (concurrency-scaling plan Q1) ----------------------
#
# The length gate in `global_search` returns before touching `session` at
# all, so these are pure-Python assertions: no DB, no fixture, `session`
# passed as None is never dereferenced on this branch.


def test_min_query_len_is_three() -> None:
    """Pins the floor itself, so a future accidental edit fails loudly here
    rather than only in the (much slower) integration suite."""
    assert MIN_QUERY_LEN == 3


@pytest.mark.parametrize("query", ["", " ", "a", "ab", "  ab  "])
async def test_queries_below_the_floor_return_empty_without_touching_the_session(
    query: str,
) -> None:
    result = await global_search(None, actor=_member([uuid.uuid4()]), q=query)  # type: ignore[arg-type]
    assert result.components == []
    assert result.vulnerabilities == []
