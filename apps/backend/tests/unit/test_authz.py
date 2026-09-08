"""
Pure unit tests for ``core.authz.can_access_group`` and
``core.authz.assert_team_access``.

Phase 2 PR 2-D folded the older, flat-only, session-less ``can_access_team``
into ``can_access_group`` (now the single "can this actor reach this
group/team?" primitive) and made ``assert_team_access`` an async wrapper
around it. These tests still run without a real database: a ``_FakeSession``
stands in for the one query ``services.group_service.can_access_group``
issues (``SELECT groups.id, groups.path WHERE groups.id = :group_id``),
answering it as a ROOT group (``path=()``) whose id is the requested
``group_id`` — with an empty ``path`` the cascade-on and cascade-off answers
agree, so these tests pin exactly the same "direct membership only" contract
the old ``can_access_team`` had, without needing Postgres or the
``GROUP_CASCADE_ENABLED`` flag. Cascade-specific behaviour (ancestor walks,
sibling/inherit/no-upward/deep-inherit) is covered against a real group tree
in ``tests/integration/test_group_cascade_wiring.py`` and
``tests/integration/test_group_cascade_list_detail_parity.py``.

These tests pin:

  - super-admin bypass via ``is_superuser`` or ``role == "super_admin"``;
  - regular members pass iff the team is in ``actor.team_ids``;
  - ``assert_team_access`` emits a single ``authz.cross_team_attempt`` log
    line on denial and raises the caller-supplied exception, with no log
    line on the happy path (so SOC dashboards aren't polluted by routine
    successes).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import structlog

from core.authz import assert_team_access, can_access_group


@dataclass
class _Actor:
    """Minimal CurrentUser-shaped fixture — only the fields the helpers read."""

    id: uuid.UUID
    role: str | None = None
    is_superuser: bool = False
    team_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


def _team() -> uuid.UUID:
    return uuid.uuid4()


class _FakeGroupRow:
    """Shaped like the ``(Group.id, Group.path)`` row ``group_service.can_access_group``
    reads — a root group (empty ``path``) whose id is the group being checked."""

    def __init__(self, group_id: uuid.UUID) -> None:
        self.id = group_id
        self.path: tuple[uuid.UUID, ...] = ()


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeSession:
    """Answers the one query ``group_service.can_access_group`` issues for a
    ROOT group matching the id being checked. Any actual database access
    beyond that single ``SELECT`` would be a bug in the code under test, not
    something this fixture needs to support."""

    def __init__(self, group_id: uuid.UUID) -> None:
        self._group_id = group_id

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(_FakeGroupRow(self._group_id))


# ---------------------------------------------------------------------------
# can_access_group (super-admin bypass + flat membership, session-backed)
# ---------------------------------------------------------------------------


async def test_can_access_group_via_membership() -> None:
    team_id = _team()
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset({team_id}))
    session = _FakeSession(team_id)
    assert await can_access_group(session, actor, team_id) is True  # type: ignore[arg-type]


async def test_cannot_access_group_when_not_member() -> None:
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset())
    team_id = _team()
    session = _FakeSession(team_id)
    assert await can_access_group(session, actor, team_id) is False  # type: ignore[arg-type]


async def test_super_admin_role_bypasses_membership() -> None:
    team_id = _team()
    actor = _Actor(id=uuid.uuid4(), role="super_admin", team_ids=frozenset())
    session = _FakeSession(team_id)
    assert await can_access_group(session, actor, team_id) is True  # type: ignore[arg-type]


async def test_is_superuser_flag_bypasses_membership() -> None:
    team_id = _team()
    actor = _Actor(id=uuid.uuid4(), is_superuser=True, team_ids=frozenset())
    session = _FakeSession(team_id)
    assert await can_access_group(session, actor, team_id) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# assert_team_access
# ---------------------------------------------------------------------------


async def test_assert_team_access_passes_silently_for_member() -> None:
    """Happy path must NOT emit a log line — keeps SOC dashboards clean."""
    team_id = _team()
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset({team_id}))
    session = _FakeSession(team_id)
    log = structlog.get_logger("test.authz")
    with structlog.testing.capture_logs() as captured:
        await assert_team_access(
            session,  # type: ignore[arg-type]
            actor,  # type: ignore[arg-type]
            team_id,
            log=log,  # type: ignore[arg-type]
            resource="project",
            resource_id="abc",
            deny=lambda: AssertionError("should not fire"),
        )
    assert captured == []


async def test_assert_team_access_logs_and_raises_on_denial() -> None:
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset())
    target_team = _team()
    session = _FakeSession(target_team)
    log = structlog.get_logger("test.authz")

    class _Forbidden(Exception):
        pass

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(_Forbidden):
            await assert_team_access(
                session,  # type: ignore[arg-type]
                actor,  # type: ignore[arg-type]
                target_team,
                log=log,  # type: ignore[arg-type]
                resource="vulnerability_finding",
                resource_id="xyz",
                deny=lambda: _Forbidden("denied"),
            )

    assert len(captured) == 1
    event = captured[0]
    assert event["event"] == "authz.cross_team_attempt"
    assert event["resource"] == "vulnerability_finding"
    assert event["resource_id"] == "xyz"
    assert event["actor_id"] == str(actor.id)
    assert event["target_team_id"] == str(target_team)


async def test_assert_team_access_supports_existence_hide_pattern() -> None:
    """The helper is also used to existence-hide cross-team reads — the
    deny callable just returns the appropriate NotFound subclass."""
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset())
    target_team = _team()
    session = _FakeSession(target_team)
    log = structlog.get_logger("test.authz")

    class _NotFound(Exception):
        pass

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(_NotFound):
            await assert_team_access(
                session,  # type: ignore[arg-type]
                actor,  # type: ignore[arg-type]
                target_team,
                log=log,  # type: ignore[arg-type]
                resource="obligation",
                resource_id="ob-1",
                deny=lambda: _NotFound("hidden"),
            )

    # The log fires regardless of which exception type the caller raises —
    # SOC tooling can correlate the rejection across both 403 and 404 paths.
    assert len(captured) == 1
    assert captured[0]["event"] == "authz.cross_team_attempt"


async def test_assert_team_access_skips_deny_callable_on_success() -> None:
    """``deny`` must not be invoked on the happy path. Otherwise constructing
    the exception eagerly each call would defeat the lazy-callable design."""
    team_id = _team()
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset({team_id}))
    session = _FakeSession(team_id)
    log = structlog.get_logger("test.authz")
    counter = {"calls": 0}

    def _track() -> Exception:
        counter["calls"] += 1
        return RuntimeError("should not be invoked")

    await assert_team_access(
        session,  # type: ignore[arg-type]
        actor,  # type: ignore[arg-type]
        team_id,
        log=log,  # type: ignore[arg-type]
        resource="project",
        resource_id="abc",
        deny=_track,
    )
    assert counter["calls"] == 0
