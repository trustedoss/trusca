# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Doc oracle for the admin health table (testing-standards hardening rule 4).

Two rows of the component table in the admin guide described something the
probes do not do.

  - ``last_24h_errors`` was documented as a count of ``ERROR``-level
    structured-log events. ``_probe_last_24h_errors`` counts scans whose
    status is ``failed`` and whose ``completed_at`` is inside the window. No
    log event is read anywhere.
  - ``active_scans`` was documented as scans in ``running`` state.
    ``_probe_active_scans`` counts ``queued`` **or** ``running``.

Neither error was reachable by a behavioural test: the probes are correct and
the API contract is stable. Only the prose was wrong, and prose is what the
operator acts on when deciding whether a number is alarming.

These tests read the probe source and require the guide to agree with it, in
both locales. They fail if a probe changes what it counts and the guide is
left behind, which is the direction the drift actually ran.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND = pathlib.Path(__file__).resolve().parents[2]

_SERVICE = BACKEND / "services" / "admin_health_service.py"
_GUIDE_EN = REPO_ROOT / "docs-site" / "docs" / "admin-guide" / "disk-and-health.md"
_GUIDE_KO = (
    REPO_ROOT
    / "docs-site"
    / "i18n"
    / "ko"
    / "docusaurus-plugin-content-docs"
    / "current"
    / "admin-guide"
    / "disk-and-health.md"
)


def _probe_body(name: str) -> str:
    """Source of one probe function, up to the next top-level definition.

    Handles both forms: most probes are ``async def``, but the redis and
    celery ones are plain ``def`` because their clients are synchronous.
    """
    source = _SERVICE.read_text(encoding="utf-8")
    for prefix in (f"async def {name}(", f"def {name}("):
        start = source.find(prefix)
        if start != -1:
            break
    else:  # pragma: no cover - a renamed probe should fail loudly
        raise AssertionError(f"admin_health_service.py has no {name}()")

    rest = source[start:]
    ends = [i for i in (rest.find("\nasync def ", 1), rest.find("\ndef ", 1)) if i != -1]
    return rest if not ends else rest[: min(ends)]


def _row(guide: pathlib.Path, component: str) -> str:
    for line in guide.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| `{component}` |"):
            return line
    raise AssertionError(f"{guide.name} has no table row for {component}")


def test_last_24h_errors_probe_counts_failed_scans() -> None:
    """Pins what the probe counts, so the doc assertions below mean something."""
    body = _probe_body("_probe_last_24h_errors")
    assert 'Scan.status == "failed"' in body
    assert "completed_at" in body


def test_active_scans_probe_counts_queued_and_running() -> None:
    body = _probe_body("_probe_active_scans")
    assert '"queued"' in body and '"running"' in body


@pytest.mark.parametrize("guide", (_GUIDE_EN, _GUIDE_KO), ids=("en", "ko"))
def test_last_24h_errors_row_does_not_cite_a_log_level(guide: pathlib.Path) -> None:
    """The row must not tie the count to a log level, in either locale.

    ``Count of ERROR-level structured-log events`` is the exact wording that
    was wrong. An operator reading it would go looking for a logging problem
    when what they have is failing scans.
    """
    row = _row(guide, "last_24h_errors")
    assert "ERROR" not in row, (
        f"{guide.name} still ties last_24h_errors to an ERROR log level; the "
        f"probe reads the scans table and never looks at a log event"
    )


@pytest.mark.parametrize(
    ("guide", "expected"),
    (
        (_GUIDE_EN, "failed"),
        (_GUIDE_KO, "실패"),
    ),
    ids=("en", "ko"),
)
def test_last_24h_errors_row_says_failed_scans(
    guide: pathlib.Path, expected: str
) -> None:
    assert expected in _row(guide, "last_24h_errors")


def test_redis_probe_uses_the_synchronous_client() -> None:
    """The import pins it: ``redis``, not ``redis.asyncio``."""
    source = _SERVICE.read_text(encoding="utf-8")
    assert "import redis as _redis" in source
    assert "redis.asyncio" not in source


@pytest.mark.parametrize("guide", (_GUIDE_EN, _GUIDE_KO), ids=("en", "ko"))
def test_redis_row_does_not_claim_an_async_client(guide: pathlib.Path) -> None:
    """The row said asyncio; the probe is a plain synchronous call.

    Minor on its own, but an operator debugging a stall wants to know whether
    this probe can block the event loop. It can.
    """
    row = _row(guide, "redis").lower()
    assert "asyncio" not in row, (
        f"{guide.name} still describes the redis probe as asyncio; it uses "
        f"the synchronous client"
    )


def _celery_ping_timeout() -> str:
    """The ping budget, read off the probe so the docs cannot drift from it."""
    body = _probe_body("_probe_celery")
    match = re.search(r"control\.ping\(timeout=([0-9.]+)\)", body)
    assert match, "could not find the control.ping timeout in _probe_celery"
    return match.group(1).rstrip("0").rstrip(".")


@pytest.mark.parametrize("guide", (_GUIDE_EN, _GUIDE_KO), ids=("en", "ko"))
def test_celery_row_states_the_real_budget(guide: pathlib.Path) -> None:
    """The row must carry the actual number, not call it configurable.

    It was documented as "the configured timeout", which invites an operator
    to go looking for a setting that does not exist. The budget is a literal
    in the probe.
    """
    row = _row(guide, "celery")
    assert _celery_ping_timeout() in row, (
        f"{guide.name} does not state the real ping budget "
        f"({_celery_ping_timeout()}s)"
    )
    for wrong in ("configured timeout", "설정 타임아웃"):
        assert wrong not in row, (
            f"{guide.name} calls the celery ping budget {wrong!r}; it is a "
            f"literal in the probe, not a setting"
        )


@pytest.mark.parametrize("guide", (_GUIDE_EN, _GUIDE_KO), ids=("en", "ko"))
def test_active_scans_row_names_both_states(guide: pathlib.Path) -> None:
    """The row must name ``queued`` as well as ``running``.

    Documenting only ``running`` understates the number an operator sees, so
    a queue that is backing up looks like a smaller problem than it is.
    """
    row = _row(guide, "active_scans")
    assert "`queued`" in row and "`running`" in row
