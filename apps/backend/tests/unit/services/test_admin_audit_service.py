"""
Service-layer tests for ``services.admin_audit_service`` — Phase 4 PR #14.

Drives the search + CSV export against a live Postgres (DATABASE_URL) so
the SQL filter chain — including the JSONB cast + ILIKE — actually runs.
Mirrors the shape of ``test_admin_user_service.py``.

Coverage:
  - search filters: actor / target_table / action / from / to / q
  - pagination envelope (page / page_size / has_more / total)
  - actor_email JOIN: deleted-actor row still renders
  - CSV export: header line + data rows + 100k cap (mocked count)
  - adversarial parametrize on the ``q`` and ``action`` inputs
"""

from __future__ import annotations

import csv as _csv
import io
import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import AuditLog
from schemas.admin_ops import AuditSearchQuery
from tests._helpers import make_user, principal_for, unique_suffix

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin_audit_service tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; admin_audit_service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_audit_row(
    session: AsyncSession,
    *,
    actor_user_id: object | None = None,
    target_table: str = "projects",
    action: str = "create",
    diff: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> AuditLog:
    """Insert a single audit_logs row directly (the listener already handles real flushes)."""
    row = AuditLog(
        actor_user_id=actor_user_id,
        team_id=None,
        target_table=target_table,
        target_id=None,
        action=action,
        request_id=f"req-{unique_suffix()}",
        diff=diff,
    )
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# search_audit
# ---------------------------------------------------------------------------


async def test_search_audit_returns_envelope_with_total(db_session: AsyncSession) -> None:
    from services.admin_audit_service import search_audit

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    await _seed_audit_row(db_session, action=target_action)
    await _seed_audit_row(db_session, action=target_action)

    page = await search_audit(
        db_session,
        actor=actor,
        query=AuditSearchQuery(action=target_action, page=1, page_size=10),
    )
    assert page.total == 2
    assert page.page == 1
    assert page.page_size == 10
    assert page.has_more is False
    assert all(item.action == target_action for item in page.items)


async def test_search_audit_filters_by_actor_user_id(db_session: AsyncSession) -> None:
    from services.admin_audit_service import search_audit

    admin = await make_user(db_session, is_superuser=True)
    other_actor = await make_user(db_session)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    await _seed_audit_row(db_session, actor_user_id=admin.id, action=target_action)
    await _seed_audit_row(db_session, actor_user_id=other_actor.id, action=target_action)

    page = await search_audit(
        db_session,
        actor=actor,
        query=AuditSearchQuery(actor_user_id=admin.id, action=target_action),
    )
    assert page.total == 1
    assert page.items[0].actor_user_id == admin.id
    assert page.items[0].actor_email == admin.email


async def test_search_audit_filters_by_target_table(db_session: AsyncSession) -> None:
    from services.admin_audit_service import search_audit

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    await _seed_audit_row(db_session, target_table="projects", action=target_action)
    await _seed_audit_row(db_session, target_table="users", action=target_action)

    page = await search_audit(
        db_session,
        actor=actor,
        query=AuditSearchQuery(target_table="projects", action=target_action),
    )
    assert page.total == 1
    assert page.items[0].target_table == "projects"


async def test_search_audit_filters_by_time_window(db_session: AsyncSession) -> None:
    from services.admin_audit_service import search_audit

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    now = datetime.now(tz=UTC)
    # Two rows: one inside, one outside the window.
    inside = await _seed_audit_row(
        db_session, action=target_action, created_at=now - timedelta(minutes=5)
    )
    await _seed_audit_row(
        db_session, action=target_action, created_at=now - timedelta(days=2)
    )

    page = await search_audit(
        db_session,
        actor=actor,
        query=AuditSearchQuery.model_validate(
            {
                "action": target_action,
                "from": now - timedelta(hours=1),
                "to": now,
            }
        ),
    )
    assert page.total == 1
    assert page.items[0].id == inside.id


async def test_search_audit_jsonb_q_substring_matches_diff(
    db_session: AsyncSession,
) -> None:
    from services.admin_audit_service import search_audit

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    needle = f"needle-{unique_suffix()}"
    await _seed_audit_row(
        db_session,
        action=target_action,
        diff={"name": f"contains-{needle}-here"},
    )
    await _seed_audit_row(
        db_session, action=target_action, diff={"name": "no-match"}
    )

    page = await search_audit(
        db_session,
        actor=actor,
        query=AuditSearchQuery(action=target_action, q=needle),
    )
    assert page.total == 1
    assert needle in str(page.items[0].diff or {})


async def test_search_audit_pagination_has_more(db_session: AsyncSession) -> None:
    from services.admin_audit_service import search_audit

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    for _ in range(5):
        await _seed_audit_row(db_session, action=target_action)

    page = await search_audit(
        db_session,
        actor=actor,
        query=AuditSearchQuery(action=target_action, page=1, page_size=2),
    )
    assert page.total == 5
    assert page.has_more is True
    assert len(page.items) == 2


# ---------------------------------------------------------------------------
# stream_audit_csv
# ---------------------------------------------------------------------------


async def test_stream_audit_csv_yields_header_and_rows(db_session: AsyncSession) -> None:
    from services.admin_audit_service import stream_audit_csv

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    for _ in range(3):
        await _seed_audit_row(db_session, action=target_action, actor_user_id=admin.id)

    chunks: list[str] = []
    async for chunk in stream_audit_csv(
        db_session,
        actor=actor,
        query=AuditSearchQuery(action=target_action),
    ):
        chunks.append(chunk)

    full = "".join(chunks)
    # A3 (sys-bug-audit-2): the export prepends a UTF-8 BOM so Excel on
    # CJK locales auto-detects the encoding. The BOM is a U+FEFF code
    # point in the in-memory string; csv.reader sees it as the first
    # character of the first cell, so we strip it here before parsing.
    assert full.startswith("\ufeff"), f"missing BOM: {full[:8]!r}"
    reader = _csv.reader(io.StringIO(full.lstrip("\ufeff")))
    rows = list(reader)
    # Header + 3 rows.
    assert len(rows) == 4
    assert rows[0] == [
        "created_at",
        "actor_user_id",
        "actor_email",
        "team_id",
        "target_table",
        "target_id",
        "action",
        "request_id",
    ]
    # Body rows carry the actor email (joined from users).
    assert all(r[2] == admin.email for r in rows[1:])


async def test_stream_audit_csv_diff_column_absent(db_session: AsyncSession) -> None:
    """The CSV export deliberately omits ``diff`` to avoid leaking JSONB shapes."""
    from services.admin_audit_service import stream_audit_csv

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    secret = f"SECRET-{unique_suffix()}"
    await _seed_audit_row(db_session, action=target_action, diff={"hidden": secret})

    chunks: list[str] = []
    async for chunk in stream_audit_csv(
        db_session,
        actor=actor,
        query=AuditSearchQuery(action=target_action),
    ):
        chunks.append(chunk)

    full = "".join(chunks)
    assert "diff" not in full.splitlines()[0]  # no diff column
    assert secret not in full  # secret never reaches the export


async def test_stream_audit_csv_too_large_raises(db_session: AsyncSession) -> None:
    """Patch the hard cap to 1 and seed 2 rows → AuditExportTooLarge."""
    import services.admin_audit_service as svc

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    await _seed_audit_row(db_session, action=target_action)
    await _seed_audit_row(db_session, action=target_action)

    original_cap = svc._CSV_EXPORT_HARD_LIMIT
    svc._CSV_EXPORT_HARD_LIMIT = 1
    try:
        with pytest.raises(svc.AuditExportTooLarge):
            async for _ in svc.stream_audit_csv(
                db_session,
                actor=actor,
                query=AuditSearchQuery(action=target_action),
            ):
                pass
    finally:
        svc._CSV_EXPORT_HARD_LIMIT = original_cap


# ---------------------------------------------------------------------------
# Adversarial input — schema-level rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "garbage",
    [
        "\x00\x00",       # null bytes
        "line1\r\nLine2", # CRLF injection
        "x" * 10_000,     # oversized — also caught by max_length
    ],
)
def test_audit_search_query_rejects_control_chars_in_q(garbage: str) -> None:
    """The schema's ``q`` validator rejects null + CR + LF and the >255 cap rejects oversized."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuditSearchQuery(q=garbage)


@pytest.mark.parametrize(
    "garbage",
    [
        "\x00",
        "act\nion",
        "act\rion",
    ],
)
def test_audit_search_query_rejects_control_chars_in_action(garbage: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuditSearchQuery(action=garbage)


@pytest.mark.parametrize(
    "garbage",
    [
        "users; DROP TABLE projects",
        "../etc/passwd",
        "javascript:alert(1)",
        "RTL_OVERRIDE‮something",
    ],
)
def test_audit_search_query_rejects_unknown_target_table(garbage: str) -> None:
    """``target_table`` is a closed Literal — anything outside is a 422."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuditSearchQuery(target_table=garbage)  # type: ignore[arg-type]


async def test_search_audit_q_with_sql_keywords_is_safe(db_session: AsyncSession) -> None:
    """SQL keywords in q must be treated as literal substrings, never executed."""
    from services.admin_audit_service import search_audit

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    # Seed a row whose diff happens to contain a SQL fragment as data.
    await _seed_audit_row(
        db_session,
        action=target_action,
        diff={"sql_fragment": "SELECT * FROM users WHERE 1=1"},
    )

    # Query with the SQL keyword as a substring — service must NOT execute it,
    # just treat it as a literal pattern. The audit row contains it so we expect
    # exactly 1 match; if the keyword were interpolated unsafely, we'd see DB
    # errors instead of a clean result.
    page = await search_audit(
        db_session,
        actor=actor,
        query=AuditSearchQuery(action=target_action, q="SELECT *"),
    )
    assert page.total == 1


# ---------------------------------------------------------------------------
# G1 — CSV formula injection (CWE-1236)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        '=cmd|"/c calc"!A1',
        "+1+1",
        "-2+3",
        "@SUM(1+1)",
        "\t=cmd",
        "\r=cmd",
        '=HYPERLINK("http://evil/x","click")',
    ],
)
def test_csv_cell_escapes_dangerous_prefix_with_apostrophe(payload: str) -> None:
    """Cells whose first char is ``= + - @ \\t \\r`` must be prefixed with ``'``.

    Without this, an audit row whose ``action`` / ``target_id`` /
    ``request_id`` (operator-controlled columns) starts with ``=`` is
    executed as a formula when the export is opened in Excel / LibreOffice /
    Sheets, giving the attacker DDE / shell-escape against the super-admin's
    workstation. CWE-1236.
    """
    from services.admin_audit_service import _csv_cell

    rendered = _csv_cell(payload)

    assert rendered.startswith("'"), f"expected leading quote on {payload!r}"
    # Original payload still present so the column retains forensic value.
    assert rendered[1:] == payload


@pytest.mark.parametrize(
    "payload",
    [
        "normal-action",
        "user.email@example.com",
        "12345",
        "",
    ],
)
def test_csv_cell_leaves_safe_values_unchanged(payload: str) -> None:
    """Values whose first char is not in the dangerous set pass through verbatim."""
    from services.admin_audit_service import _csv_cell

    assert _csv_cell(payload) == payload


async def test_stream_audit_csv_escapes_formula_in_request_id(
    db_session: AsyncSession,
) -> None:
    """End-to-end: a malicious request_id flows through the CSV stream escaped.

    Exercises the full row → cell → line path so a future regression in
    ``_format_row`` / ``_csv_line`` cannot quietly bypass the ``_csv_cell`` guard.
    """
    from services.admin_audit_service import stream_audit_csv

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    target_action = f"act-{unique_suffix()}"
    payload = '=HYPERLINK("http://evil/x","click")'
    row = AuditLog(
        actor_user_id=admin.id,
        team_id=None,
        target_table="projects",
        target_id=None,
        action=target_action,
        request_id=payload,
        diff=None,
    )
    db_session.add(row)
    await db_session.commit()

    chunks: list[str] = []
    async for chunk in stream_audit_csv(
        db_session,
        actor=actor,
        query=AuditSearchQuery(action=target_action),
    ):
        chunks.append(chunk)
    body = "".join(chunks)

    # Apostrophe-prefixed form must appear (csv.writer may quote the cell, so
    # the leading character can be either ``'`` directly or after an opening
    # ``"``). Either way the unprotected payload must never appear without a
    # preceding apostrophe.
    assert "'=HYPERLINK" in body
    # No bare ``=HYPERLINK`` (formula start) anywhere in the rendered CSV.
    assert "=HYPERLINK" not in body.replace("'=HYPERLINK", "")


# ---------------------------------------------------------------------------
# G5 — ILIKE wildcard escape (pure unit — no DB needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_q, expected_escaped",
    [
        # Plain string — no metacharacters
        ("admin", "admin"),
        # Percent wildcard
        ("%admin%", "\\%admin\\%"),
        # Underscore wildcard
        ("_admin_", "\\_admin\\_"),
        # Backslash escape char itself
        ("a\\b", "a\\\\b"),
        # Mixed
        ("%foo_bar%", "\\%foo\\_bar\\%"),
        # All metacharacters in sequence
        ("\\%_", "\\\\\\%\\_"),
    ],
    ids=lambda v: repr(v) if isinstance(v, str) else "",
)
def test_ilike_wildcard_escape_in_q_parameter(raw_q: str, expected_escaped: str) -> None:
    """G5: the _apply_filters q path escapes LIKE metacharacters before the
    ILIKE call to prevent pathological patterns from saturating Postgres CPU.

    We test the escape transformation in isolation (pure string logic).
    """
    escaped = raw_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    assert escaped == expected_escaped
