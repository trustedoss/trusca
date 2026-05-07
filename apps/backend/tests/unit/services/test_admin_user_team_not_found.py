"""
Pure-unit tests for the F9 ``TeamNotFound`` translation in
``services.admin_user_service``.

These run without DATABASE_URL — the integration shape (preflight + commit-
time IntegrityError catch) is covered by the DB-backed cases inside
``test_admin_user_service.py``. Here we pin:

  - ``TeamNotFound.extensions`` carries ``{"team_id": "<uuid>"}`` when given,
    empty dict otherwise.
  - ``_is_team_fk_violation`` matches Postgres SQLSTATE 23503 (FK violation)
    AND a ``team_id`` / ``teams`` substring in the message — and rejects
    everything else, so an unrelated 500 stays a 500.
"""

from __future__ import annotations

import uuid


def test_team_not_found_carries_team_id_extension() -> None:
    from services.admin_user_service import TeamNotFound

    team_id = uuid.uuid4()
    exc = TeamNotFound("team gone", team_id=team_id)
    assert exc.extensions == {"team_id": str(team_id)}
    assert str(exc) == "team gone"


def test_team_not_found_extension_default_when_no_team_id() -> None:
    """The extension stays empty when no team_id is supplied — defensive."""
    from services.admin_user_service import TeamNotFound

    exc = TeamNotFound("oops")
    assert exc.extensions == {}


def test_team_not_found_inherits_admin_user_error_status() -> None:
    """Pin status_code = 422 + correct title for the router translator."""
    from services.admin_user_service import AdminUserError, TeamNotFound

    exc = TeamNotFound("missing team")
    assert isinstance(exc, AdminUserError)
    assert exc.status_code == 422
    assert exc.title == "Team Not Found"


def test_is_team_fk_violation_recognises_pgcode_23503_team_id() -> None:
    """The IntegrityError dispatcher must spot the FK-violation SQLSTATE."""
    from services.admin_user_service import _is_team_fk_violation

    class _FakeOrig:
        pgcode = "23503"

        def __str__(self) -> str:
            return 'insert or update on "memberships" violates fk on team_id'

    class _FakeIntegrityError(Exception):
        orig = _FakeOrig()

    assert _is_team_fk_violation(_FakeIntegrityError()) is True  # type: ignore[arg-type]


def test_is_team_fk_violation_recognises_pgcode_23503_teams_table() -> None:
    """Variant: psycopg may name the referenced table (``teams``) instead of column."""
    from services.admin_user_service import _is_team_fk_violation

    class _FakeOrig:
        pgcode = "23503"

        def __str__(self) -> str:
            return 'Key (team_id)=(...) is not present in table "teams"'

    class _FakeIntegrityError(Exception):
        orig = _FakeOrig()

    assert _is_team_fk_violation(_FakeIntegrityError()) is True  # type: ignore[arg-type]


def test_is_team_fk_violation_recognises_sqlstate_attribute() -> None:
    """Some drivers expose the code as ``sqlstate`` instead of ``pgcode``."""
    from services.admin_user_service import _is_team_fk_violation

    class _FakeOrig:
        sqlstate = "23503"

        def __str__(self) -> str:
            return "fk_membership_team_id"

    class _FakeIntegrityError(Exception):
        orig = _FakeOrig()

    assert _is_team_fk_violation(_FakeIntegrityError()) is True  # type: ignore[arg-type]


def test_is_team_fk_violation_ignores_non_team_fk() -> None:
    """A user_id FK violation is NOT a TeamNotFound — keep the 500."""
    from services.admin_user_service import _is_team_fk_violation

    class _FakeOrig:
        pgcode = "23503"

        def __str__(self) -> str:
            return 'insert or update on "memberships" violates fk on user_id'

    class _FakeIntegrityError(Exception):
        orig = _FakeOrig()

    assert _is_team_fk_violation(_FakeIntegrityError()) is False  # type: ignore[arg-type]


def test_is_team_fk_violation_ignores_unrelated_sqlstate() -> None:
    """Unique violation (23505) → not a 422; let it surface as 500."""
    from services.admin_user_service import _is_team_fk_violation

    class _FakeOrig:
        pgcode = "23505"

        def __str__(self) -> str:
            return "duplicate key value violates unique constraint"

    class _FakeIntegrityError(Exception):
        orig = _FakeOrig()

    assert _is_team_fk_violation(_FakeIntegrityError()) is False  # type: ignore[arg-type]


def test_is_team_fk_violation_handles_missing_orig() -> None:
    """A bare IntegrityError without an ``orig`` attribute MUST NOT crash."""
    from services.admin_user_service import _is_team_fk_violation

    class _BareIntegrityError(Exception):
        pass

    assert _is_team_fk_violation(_BareIntegrityError()) is False  # type: ignore[arg-type]
