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
    """Source of one probe function, up to the next top-level ``async def``."""
    source = _SERVICE.read_text(encoding="utf-8")
    start = source.index(f"async def {name}(")
    rest = source[start:]
    nxt = rest.find("\nasync def ", 1)
    return rest if nxt == -1 else rest[:nxt]


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


@pytest.mark.parametrize("guide", (_GUIDE_EN, _GUIDE_KO), ids=("en", "ko"))
def test_active_scans_row_names_both_states(guide: pathlib.Path) -> None:
    """The row must name ``queued`` as well as ``running``.

    Documenting only ``running`` understates the number an operator sees, so
    a queue that is backing up looks like a smaller problem than it is.
    """
    row = _row(guide, "active_scans")
    assert "`queued`" in row and "`running`" in row
