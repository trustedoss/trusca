"""
Malicious catalog refresh beat (#26, MAL-2b) — unit tests.

The beat's reason for existing is the clear → flagged transition: a package
that was fine when it was last scanned and is not fine now. No scan produces
that finding, because nobody re-scans an old release. So the transition must
raise a notification, and the notification must have a publisher — this repo
already carries several notification kinds nothing ever emits, and a silent
kind is indistinguishable from a working one until an incident.

The other half is failure posture. A beat that raises takes the schedule down
with it, so every exit path writes the status row and none of them propagate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest


class _Row:
    """Stand-in for a ComponentVersion the re-stamp pass walks."""

    def __init__(self, purl: str, state: str | None) -> None:
        self.id = uuid.uuid4()
        self.purl_with_version = purl
        self.version = purl.rsplit("@", 1)[-1]
        self.malicious_state = state
        self.malicious_id: str | None = None
        self.malicious_source: str | None = None
        self.malicious_evaluated_at: datetime | None = None


class _Result:
    def __init__(self, rows: list[Any], scalar: Any = 0) -> None:
        self._rows = rows
        self._scalar = scalar

    def scalars(self) -> Any:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._scalar


class _Session:
    """Minimal session: returns the seeded rows, records commits."""

    def __init__(self, rows: list[Any], project_rows: list[Any] | None = None) -> None:
        self._rows = rows
        self._project_rows = project_rows or []
        self.commits = 0
        self.executed = 0

    def execute(self, stmt: Any) -> _Result:
        self.executed += 1
        text = str(stmt)
        if "count" in text.lower():
            flagged = sum(1 for r in self._rows if r.malicious_state == "flagged")
            return _Result([], scalar=flagged)
        if "projects" in text.lower():
            return _Result(self._project_rows)
        return _Result(self._rows)

    def commit(self) -> None:
        self.commits += 1

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Wire the task's collaborators to fakes and capture what it enqueued."""
    import tasks.malicious_catalog_refresh as mod

    sent: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []

    monkeypatch.setattr(mod, "_persist_sync_state", lambda s: persisted.append(dict(s)))
    monkeypatch.setattr(mod, "_notify_newly_flagged", lambda session, newly: len(newly))

    return mod, sent, persisted


def _install_session(monkeypatch: pytest.MonkeyPatch, session: _Session) -> None:
    import core.db as db

    monkeypatch.setattr(db, "sync_session_scope", lambda: session)


def test_disabled_writes_the_status_row_and_does_nothing_else(
    patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod, _, persisted = patched
    monkeypatch.setenv("MALICIOUS_ENABLED", "false")

    out = mod.refresh_malicious_catalog.apply().get()

    assert out["skipped"] is True
    assert out["skipped_reason"] == "disabled"
    assert out["stamped"] == 0
    # The row is written even here — a panel that shows nothing cannot say
    # whether the beat is off or broken.
    assert persisted and persisted[-1]["skipped_reason"] == "disabled"


def test_fetch_is_off_by_default_but_the_restamp_still_runs(
    patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half that needs no network must not be gated on the half that does.

    This is what lets an air-gapped install pick up a newer snapshot from a
    release upgrade without ever reaching the internet.
    """
    mod, _, _ = patched
    monkeypatch.delenv("MALICIOUS_REFRESH_ENABLED", raising=False)
    session = _Session([_Row("pkg:npm/ok@1.0.0", None)])
    _install_session(monkeypatch, session)

    out = mod.refresh_malicious_catalog.apply().get()

    assert out["skipped"] is True
    assert out["skipped_reason"] == "refresh_disabled"
    # Re-stamp ran: the previously unassessed row now carries a verdict.
    assert out["stamped"] >= 1
    assert session.commits >= 1


def test_a_clear_row_turning_flagged_is_reported_as_newly_flagged(
    patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transition the beat exists to catch."""
    mod, _, _ = patched
    from services.malicious import malicious_catalog

    index = malicious_catalog.load_index()
    assert index is not None
    malicious_purl = next(
        k for k in index.packages if k not in index.versions and k.startswith("pkg:")
    )

    row = _Row(f"{malicious_purl}@1.0.0", "clear")
    session = _Session([row])
    _install_session(monkeypatch, session)

    out = mod.refresh_malicious_catalog.apply().get()

    assert row.malicious_state == "flagged"
    assert out["newly_flagged"] == 1
    # Publisher exists — a kind nothing emits is worse than no kind at all.
    assert out["notifications_enqueued"] == 1


def test_a_row_that_was_already_flagged_is_not_reported_again(
    patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weekly cadence must not re-alert on the same package forever."""
    mod, _, _ = patched
    from services.malicious import malicious_catalog

    index = malicious_catalog.load_index()
    assert index is not None
    malicious_purl = next(
        k for k in index.packages if k not in index.versions and k.startswith("pkg:")
    )

    row = _Row(f"{malicious_purl}@1.0.0", "flagged")
    row.malicious_id = index.packages[malicious_purl]
    row.malicious_source = f"osv.dev@{index.snapshot}"
    session = _Session([row])
    _install_session(monkeypatch, session)

    out = mod.refresh_malicious_catalog.apply().get()

    assert out["newly_flagged"] == 0
    assert out["notifications_enqueued"] == 0


def test_an_exception_mid_pass_is_recorded_not_raised(
    patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A beat that raises takes the schedule down with it."""
    mod, _, persisted = patched

    class _Boom:
        def __enter__(self) -> Any:
            raise RuntimeError("db gone")

        def __exit__(self, *exc: object) -> None:
            return None

    import core.db as db

    monkeypatch.setattr(db, "sync_session_scope", lambda: _Boom())

    out = mod.refresh_malicious_catalog.apply().get()

    assert out["skipped_reason"] == "unexpected:RuntimeError"
    assert persisted, "the status row must be written on the failure path too"


def test_the_notification_actually_reaches_the_dispatch_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise `_notify_newly_flagged` itself, not a stand-in for it.

    The tests above replace it, which is fine for the beat's bookkeeping but
    proves nothing about the kind name, the argument order, or whether anyone
    is called at all. This repo already carries notification kinds that no
    code path emits; the way that happens is a test that mocks the sender.
    """
    import tasks.malicious_catalog_refresh as mod
    import tasks.notify as notify

    project_id, team_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        mod,
        "_affected_projects",
        lambda session, purls: {
            purls[0]: [(project_id, "payments", team_id)],
        },
    )
    monkeypatch.setattr(mod, "_team_member_user_ids", lambda session, tid: [uuid.uuid4()])

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class _Task:
        @staticmethod
        def delay(*args: Any, **kwargs: Any) -> None:
            calls.append((args, kwargs))

    monkeypatch.setattr(notify, "send_notification_task", _Task)

    sent = mod._notify_newly_flagged(
        _Session([]), [("pkg:npm/evil@1.0.0", "MAL-2026-1")]
    )

    assert sent == 1
    (args, kwargs) = calls[0]
    assert args[0] == "malicious_detected"
    # In-app only, matching vuln_sla_breach — no outbound template needed yet.
    assert args[2] == []
    assert kwargs["in_app_target_table"] == "projects"
    assert str(project_id) in kwargs["in_app_link"]
    # The body must prescribe the response; an upgrade is the wrong action.
    assert "rotate" in kwargs["in_app_body"]


def test_a_flagged_package_nobody_ships_notifies_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verdict still gets written; there is simply no team to tell."""
    import tasks.malicious_catalog_refresh as mod

    monkeypatch.setattr(mod, "_affected_projects", lambda session, purls: {})

    assert mod._notify_newly_flagged(_Session([]), [("pkg:npm/x@1.0.0", None)]) == 0



# ---------------------------------------------------------------------------
# _fetch_half failure paths
#
# Four ways the refresh gives up, each leaving the previous snapshot in place
# and recording why. None of them was covered before: the tests above drive
# the task body, which stubs the fetch out entirely. These call the fetch half
# directly, which is also the only way to reach the two "the builder said it
# worked but the result is unusable" branches.
# ---------------------------------------------------------------------------


def _fetch_summary() -> dict[str, Any]:
    return {"skipped_reason": None}


def _stub_load_index(*results: Any) -> Any:
    """Stand-in for the cached ``load_index``, returning *results* in order.

    It has to carry ``cache_clear`` because the fetch half calls that before
    re-reading, and a plain function does not have one.
    """
    remaining = iter(results)

    def _load() -> Any:
        return next(remaining)

    _load.cache_clear = lambda: None  # type: ignore[attr-defined]
    return _load


def test_fetch_half_records_the_builder_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception out of the builder names the class, not the message.

    The message can carry a feed URL with credentials in it, and the column
    is 64 characters wide.
    """
    import tasks.malicious_catalog_refresh as mod

    monkeypatch.setenv("MALICIOUS_REFRESH_ENABLED", "true")

    def _boom() -> int:
        raise TimeoutError("https://user:secret@feed.example/all.json timed out")

    monkeypatch.setitem(
        __import__("sys").modules,
        "scripts.refresh_malicious_snapshot",
        type("_M", (), {"main": staticmethod(_boom)}),
    )

    summary = _fetch_summary()
    mod._fetch_half(summary)

    assert summary["skipped_reason"] == "unexpected:TimeoutError"
    assert "secret" not in summary["skipped_reason"]


def test_fetch_half_records_a_nonzero_builder_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The builder writes nothing on failure, so the old snapshot survives."""
    import tasks.malicious_catalog_refresh as mod

    monkeypatch.setenv("MALICIOUS_REFRESH_ENABLED", "true")
    monkeypatch.setitem(
        __import__("sys").modules,
        "scripts.refresh_malicious_snapshot",
        type("_M", (), {"main": staticmethod(lambda: 1)}),
    )

    summary = _fetch_summary()
    mod._fetch_half(summary)

    assert summary["skipped_reason"] == "feed_unavailable"


def test_fetch_half_records_an_unloadable_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 0 but nothing loads back. Treated as a failed fetch, not as empty.

    Reading it as "the catalog is now empty" would clear every existing
    verdict, so the distinction matters.
    """
    import tasks.malicious_catalog_refresh as mod
    from services.malicious import malicious_catalog

    monkeypatch.setenv("MALICIOUS_REFRESH_ENABLED", "true")
    monkeypatch.setitem(
        __import__("sys").modules,
        "scripts.refresh_malicious_snapshot",
        type("_M", (), {"main": staticmethod(lambda: 0)}),
    )
    monkeypatch.setattr(malicious_catalog, "load_index", _stub_load_index(None, None))

    summary = _fetch_summary()
    mod._fetch_half(summary)

    assert summary["skipped_reason"] == "feed_unavailable"


def test_fetch_half_rejects_a_collapsed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A snapshot that lost more than half its packages is not accepted.

    The builder has its own floor; this one catches the case where it wrote a
    file that still collapsed relative to what we were running on.
    """
    import tasks.malicious_catalog_refresh as mod
    from services.malicious import malicious_catalog

    monkeypatch.setenv("MALICIOUS_REFRESH_ENABLED", "true")
    monkeypatch.setitem(
        __import__("sys").modules,
        "scripts.refresh_malicious_snapshot",
        type("_M", (), {"main": staticmethod(lambda: 0)}),
    )

    before = type("_I", (), {"packages": {f"p{i}": {} for i in range(100)}})()
    after = type("_I", (), {"packages": {f"p{i}": {} for i in range(10)}})()
    monkeypatch.setattr(malicious_catalog, "load_index", _stub_load_index(before, after))

    summary = _fetch_summary()
    mod._fetch_half(summary)

    assert summary["skipped_reason"] == "feed_below_sanity_floor"
