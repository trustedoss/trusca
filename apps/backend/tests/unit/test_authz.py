"""
Pure unit tests for ``core.authz.can_access_team`` and
``core.authz.assert_team_access``.

The helpers underpin the cross-team gate in five service modules. These
tests pin:

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

import pytest
import structlog

from core.authz import assert_team_access, can_access_team


@dataclass
class _Actor:
    """Minimal CurrentUser-shaped fixture — only the fields the helpers read."""

    id: uuid.UUID
    role: str | None = None
    is_superuser: bool = False
    team_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


def _team() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# can_access_team
# ---------------------------------------------------------------------------


def test_can_access_team_via_membership() -> None:
    team_id = _team()
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset({team_id}))
    assert can_access_team(actor, team_id) is True  # type: ignore[arg-type]


def test_cannot_access_team_when_not_member() -> None:
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset())
    assert can_access_team(actor, _team()) is False  # type: ignore[arg-type]


def test_super_admin_role_bypasses_membership() -> None:
    actor = _Actor(id=uuid.uuid4(), role="super_admin", team_ids=frozenset())
    assert can_access_team(actor, _team()) is True  # type: ignore[arg-type]


def test_is_superuser_flag_bypasses_membership() -> None:
    actor = _Actor(id=uuid.uuid4(), is_superuser=True, team_ids=frozenset())
    assert can_access_team(actor, _team()) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# assert_team_access
# ---------------------------------------------------------------------------


def test_assert_team_access_passes_silently_for_member() -> None:
    """Happy path must NOT emit a log line — keeps SOC dashboards clean."""
    team_id = _team()
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset({team_id}))
    log = structlog.get_logger("test.authz")
    with structlog.testing.capture_logs() as captured:
        assert_team_access(
            actor,  # type: ignore[arg-type]
            team_id,
            log=log,  # type: ignore[arg-type]
            resource="project",
            resource_id="abc",
            deny=lambda: AssertionError("should not fire"),
        )
    assert captured == []


def test_assert_team_access_logs_and_raises_on_denial() -> None:
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset())
    target_team = _team()
    log = structlog.get_logger("test.authz")

    class _Forbidden(Exception):
        pass

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(_Forbidden):
            assert_team_access(
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


def test_assert_team_access_supports_existence_hide_pattern() -> None:
    """The helper is also used to existence-hide cross-team reads — the
    deny callable just returns the appropriate NotFound subclass."""
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset())
    target_team = _team()
    log = structlog.get_logger("test.authz")

    class _NotFound(Exception):
        pass

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(_NotFound):
            assert_team_access(
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


def test_assert_team_access_skips_deny_callable_on_success() -> None:
    """``deny`` must not be invoked on the happy path. Otherwise constructing
    the exception eagerly each call would defeat the lazy-callable design."""
    team_id = _team()
    actor = _Actor(id=uuid.uuid4(), team_ids=frozenset({team_id}))
    log = structlog.get_logger("test.authz")
    counter = {"calls": 0}

    def _track() -> Exception:
        counter["calls"] += 1
        return RuntimeError("should not be invoked")

    assert_team_access(
        actor,  # type: ignore[arg-type]
        team_id,
        log=log,  # type: ignore[arg-type]
        resource="project",
        resource_id="abc",
        deny=_track,
    )
    assert counter["calls"] == 0
