"""The flag has to change the verdict in both directions, and only it.

Three states matter and all three are asserted: with the flag set an absent
database fails, without it the same absence skips, and a working database
passes either way. Testing only the first would leave the local behaviour
this helper exists to preserve unprotected.
"""

from __future__ import annotations

import subprocess

import pytest

from tests import _db_required


@pytest.fixture(autouse=True)
def _forget_the_attempt():
    _db_required._reset_for_testing()
    yield
    _db_required._reset_for_testing()


def test_absent_database_url_skips_when_the_flag_is_off(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(_db_required.REQUIRE_ENV, raising=False)
    with pytest.raises(BaseException) as caught:
        _db_required.require_database_url()
    assert caught.typename == "Skipped", caught.typename


def test_absent_database_url_fails_when_the_flag_is_on(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(_db_required.REQUIRE_ENV, "1")
    with pytest.raises(BaseException) as caught:
        _db_required.require_database_url()
    assert caught.typename == "Failed", caught.typename
    # The message has to name the flag, or the reader on CI sees a failure
    # whose cause is an environment variable they cannot see from the log.
    assert _db_required.REQUIRE_ENV in str(caught.value)


def test_a_present_database_url_is_returned_either_way(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    for flag in ("", "1"):
        monkeypatch.setenv(_db_required.REQUIRE_ENV, flag)
        assert _db_required.require_database_url().endswith("/d")


@pytest.mark.parametrize(
    ("flag", "expected"),
    [("", "Skipped"), ("1", "Failed")],
)
def test_a_failed_migration_follows_the_same_rule(monkeypatch, flag, expected) -> None:  # noqa: ANN001
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv(_db_required.REQUIRE_ENV, flag)

    def _fails(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["alembic", "upgrade", "head"],
            returncode=1,
            stdout="",
            stderr="relation \"widgets\" already exists",
        )

    monkeypatch.setattr("tests._db_required.subprocess.run", _fails)
    with pytest.raises(BaseException) as caught:
        _db_required.migrate_to_head()
    assert caught.typename == expected, caught.typename
    # The migration's own stderr is the diagnosis; dropping it leaves the
    # reader with tests that did not run and no reason they did not.
    assert "widgets" in str(caught.value)


def test_a_successful_migration_returns_quietly(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv(_db_required.REQUIRE_ENV, "1")
    monkeypatch.setattr(
        "tests._db_required.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    _db_required.migrate_to_head()  # returns nothing; not raising is the assertion


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_the_flag_reads_the_usual_spellings_of_true(monkeypatch, value) -> None:  # noqa: ANN001
    monkeypatch.setenv(_db_required.REQUIRE_ENV, value)
    assert _db_required.database_is_required() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_everything_else_leaves_the_database_optional(monkeypatch, value) -> None:  # noqa: ANN001
    monkeypatch.setenv(_db_required.REQUIRE_ENV, value)
    assert _db_required.database_is_required() is False


def test_the_migration_is_attempted_once_and_the_failure_is_remembered(monkeypatch) -> None:  # noqa: ANN001
    """The measured defect: 193 modules each ran their own migration, so after
    the first failure every later module reported a duplicate table instead of
    the real error. Retrying cannot succeed - the broken migration breaks
    again - it only replaces the true diagnosis with a false one."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.delenv(_db_required.REQUIRE_ENV, raising=False)
    calls: list[int] = []

    def _fails_the_first_way(*_args, **_kwargs):
        calls.append(1)
        # A second run of a chain that stopped partway reports something else
        # entirely; if this is ever seen, the attempt was not cached.
        stderr = (
            "the migration raised" if len(calls) == 1
            else 'relation "widgets" already exists'
        )
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)

    monkeypatch.setattr("tests._db_required.subprocess.run", _fails_the_first_way)

    seen = []
    for _ in range(3):
        with pytest.raises(BaseException) as caught:
            _db_required.migrate_to_head()
        seen.append(str(caught.value))

    assert len(calls) == 1, f"alembic was run {len(calls)} times, not once"
    for message in seen:
        assert "the migration raised" in message
        assert "already exists" not in message


def test_a_success_is_not_repeated_either(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    calls: list[int] = []

    def _succeeds(*_args, **_kwargs):
        calls.append(1)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("tests._db_required.subprocess.run", _succeeds)
    for _ in range(5):
        _db_required.migrate_to_head()
    assert len(calls) == 1, f"alembic was run {len(calls)} times, not once"
