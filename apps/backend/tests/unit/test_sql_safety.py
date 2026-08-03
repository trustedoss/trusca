"""
Pure unit tests for `core.sql_safety.escape_like`.

The helper neutralises PostgreSQL ``LIKE`` wildcards so user input wrapped
into an ``%...%`` pattern cannot be used to widen the match. Each test pins
one literal-vs-wildcard contract; together they fix the ESCAPE clause
contract for every service that builds search filters (vulnerability /
license / obligation).
"""

from __future__ import annotations

from sqlalchemy import literal_column, select

from core.sql_safety import escape_like


def test_escape_like_passes_through_safe_value() -> None:
    """Plain text without wildcard chars must be returned verbatim."""
    assert escape_like("hello world") == "hello world"
    assert escape_like("") == ""


def test_escape_like_escapes_percent() -> None:
    """`%` is the multi-char wildcard. We prefix it with a single backslash."""
    assert escape_like("50% off") == "50\\% off"


def test_escape_like_escapes_underscore() -> None:
    """`_` is the single-char wildcard."""
    assert escape_like("foo_bar") == "foo\\_bar"


def test_escape_like_escapes_backslash() -> None:
    """The escape char itself must be escaped, otherwise the user can disable
    the ESCAPE clause by closing it (`\\\\`) before injecting `%`."""
    assert escape_like("path\\with\\backslash") == "path\\\\with\\\\backslash"


def test_escape_like_combined() -> None:
    """All three wildcard chars in the same string."""
    assert escape_like("100%_safe\\path") == "100\\%\\_safe\\\\path"


def test_escape_like_round_trip_with_sqlalchemy_ilike() -> None:
    """
    The compiled pattern must wrap the value in `%...%` and the rendered SQL
    must include an ESCAPE clause when the call site passes ``escape='\\\\'``.
    This pins that the helper's output is compatible with SQLAlchemy's
    ``.ilike(pattern, escape='\\\\')`` — the contract every service relies on.
    """
    safe = escape_like("a%b")
    pattern = f"%{safe}%"
    stmt = select(literal_column("col").ilike(pattern, escape="\\"))
    rendered = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "ESCAPE '\\'" in rendered
    # The literal `%` from the user input survives as `\%` inside the pattern.
    assert "\\%" in rendered
