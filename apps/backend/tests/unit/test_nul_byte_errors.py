"""A NUL byte in a text value is the caller's mistake, not a server fault.

Postgres cannot store U+0000 in `text` or `jsonb`. The driver raises, and
because the raise happens at INSERT time - after validation, after the handler
has run - nothing upstream catches it, so it surfaced as a 500 on every
endpoint that stores a caller-supplied string. Schema fuzzing found it on
saved-searches; probing showed the same on scan metadata and on an ordinary
`name` column.

`core.errors` turns exactly that case into a 422 and leaves every other
database error alone. These tests cover the discrimination, which is the part
that is easy to get wrong: matching too broadly would hide real faults.
"""

from __future__ import annotations

from typing import cast

from core.errors import _is_untranslatable_character


def _named(name: str, sqlstate: str | None = None) -> Exception:
    """An exception whose class name matches what asyncpg would raise.

    The detector matches by type name and SQLSTATE rather than by importing
    asyncpg's exception classes, so a stand-in built here exercises the same
    path the driver would.
    """
    exc = cast(Exception, type(name, (Exception,), {})(name))
    if sqlstate is not None:
        exc.sqlstate = sqlstate  # type: ignore[attr-defined]
    return exc


def test_detects_jsonb_variant() -> None:
    """jsonb columns raise untranslatable_character."""
    assert _is_untranslatable_character(_named("UntranslatableCharacterError"))


def test_detects_text_column_variant() -> None:
    """Plain text columns raise character_not_in_repertoire instead.

    Matching only the jsonb name left ordinary string fields returning 500,
    which is how this was found the second time.
    """
    assert _is_untranslatable_character(_named("CharacterNotInRepertoireError"))


def test_detects_by_sqlstate_when_the_name_is_unfamiliar() -> None:
    for sqlstate in ("22P05", "22021"):
        assert _is_untranslatable_character(_named("SomeOtherError", sqlstate))


def test_follows_the_orig_chain() -> None:
    """SQLAlchemy wraps the driver error; the cause is reached through .orig."""
    inner = _named("UntranslatableCharacterError")
    outer = Exception("DBAPIError")
    outer.orig = inner  # type: ignore[attr-defined]
    assert _is_untranslatable_character(outer)


def test_follows_the_cause_chain() -> None:
    inner = _named("CharacterNotInRepertoireError")
    outer = Exception("wrapper")
    outer.__cause__ = inner
    assert _is_untranslatable_character(outer)


def test_leaves_other_database_errors_alone() -> None:
    """Anything else stays a 500 and keeps its traceback."""
    for name in ("UniqueViolationError", "ForeignKeyViolationError",
                 "ConnectionDoesNotExistError", "DataError"):
        assert not _is_untranslatable_character(_named(name))
    assert not _is_untranslatable_character(_named("SomeError", "23505"))


def test_a_cyclic_cause_chain_terminates() -> None:
    """A self-referencing chain must not spin."""
    a = Exception("a")
    b = Exception("b")
    a.__cause__ = b
    b.__cause__ = a
    assert not _is_untranslatable_character(a)
