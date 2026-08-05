"""Unit tests — malicious stamping inside ``persist_sbom_components`` (#26).

The fixture is a REAL cdxgen output (``real_cyclonedx_node_dev.json``, 487
components from an actual Node project) with three entries appended that the
vendored snapshot genuinely lists. Density matters here: a hand-made
three-component SBOM would exercise the matcher but not the thing that
actually breaks — a scoped npm name travelling through the real persist path
where ``purl`` and ``bom-ref`` disagree about encoding.

Expected verdicts:

  @ctrl/tinycolor@4.1.1        → flagged  (scoped, every version malicious)
  @0xlr/clerk-auth@999.0.0     → flagged  (version-pinned, named version)
  @0xlr/clerk-auth@1.0.0       → clear    (version-pinned, other version)
  every other real component   → clear    (consulted, not listed)

Also pinned: rerun idempotency, MALICIOUS_ENABLED=false skips entirely, and —
the promise this design rests on — the signal never creates a finding and
never reaches the severity axis.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "sbom"
    / "real_cyclonedx_node_malicious.json"
)

FLAGGED_SCOPED = "pkg:npm/%40ctrl/tinycolor@4.1.1"
FLAGGED_PINNED = "pkg:npm/%400xlr/clerk-auth@999.0.0"
CLEAR_PINNED = "pkg:npm/%400xlr/clerk-auth@1.0.0"


class _FakeComponent:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeComponentVersion:
    def __init__(self, purl: str) -> None:
        self.id = uuid.uuid4()
        self.purl_with_version = purl
        self.eol_state: str | None = None
        self.eol_product: str | None = None
        self.eol_cycle: str | None = None
        self.eol_date: Any = None
        self.eol_source: str | None = None
        self.eol_evaluated_at: Any = None
        self.currency_state: str | None = None
        self.currency_latest: str | None = None
        self.currency_latest_release_date: Any = None
        self.currency_evaluated_at: Any = None
        self.malicious_state: str | None = None
        self.malicious_id: str | None = None
        self.malicious_source: str | None = None
        self.malicious_evaluated_at: Any = None


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


@pytest.fixture
def cv_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeComponentVersion]:
    """Stub persist helpers; capture ComponentVersion fakes keyed by purl."""
    registry: dict[str, _FakeComponentVersion] = {}

    def _get_cv(
        session: Any, *, component: Any, version: str, purl_with_version: str
    ) -> _FakeComponentVersion:
        if purl_with_version not in registry:
            registry[purl_with_version] = _FakeComponentVersion(purl_with_version)
        return registry[purl_with_version]

    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component",
        lambda session, *, purl, name, package_type: _FakeComponent(),
    )
    monkeypatch.setattr("tasks.scan_source._get_or_create_component_version", _get_cv)
    monkeypatch.setattr(
        "tasks.scan_source._persist_component_licenses",
        lambda session, *, scan_uuid, component_version_id, cdxgen_component, purl: None,
    )
    monkeypatch.setattr(
        "tasks.scan_source._persist_dependency_graph",
        lambda session, **kwargs: None,
    )
    return registry


def _sbom() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def _persist(session: _FakeSession) -> None:
    from tasks.scan_source import persist_sbom_components

    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=_sbom())


def test_flags_the_snapshot_entries_and_clears_the_rest(
    cv_registry: dict[str, _FakeComponentVersion],
) -> None:
    _persist(_FakeSession())

    scoped = cv_registry[FLAGGED_SCOPED]
    assert scoped.malicious_state == "flagged"
    assert scoped.malicious_id == "MAL-2025-47141"
    assert scoped.malicious_source is not None
    assert scoped.malicious_source.startswith("osv.dev@")
    assert scoped.malicious_evaluated_at is not None

    assert cv_registry[FLAGGED_PINNED].malicious_state == "flagged"
    # The same package at a version the advisory did not name is not condemned.
    assert cv_registry[CLEAR_PINNED].malicious_state == "clear"
    assert cv_registry[CLEAR_PINNED].malicious_id is None

    # The 487 real components are all consulted and none are listed. `clear`,
    # not NULL — the deny list looked at them.
    ordinary = [
        cv
        for purl, cv in cv_registry.items()
        if purl not in {FLAGGED_SCOPED, FLAGGED_PINNED, CLEAR_PINNED}
    ]
    assert len(ordinary) > 400, "fixture should carry real-world density"
    assert all(cv.malicious_state == "clear" for cv in ordinary)


def test_scoped_packages_survive_the_encoding_round_trip(
    cv_registry: dict[str, _FakeComponentVersion],
) -> None:
    """The failure this feature dies of is silent, so it gets its own test.

    cdxgen writes a scoped name twice per component: ``purl`` encodes the
    scope (``%40``) and ``bom-ref`` does not. The snapshot's keys are encoded.
    If persist ever switched to the bom-ref spelling, or the matcher started
    normalising, every scoped package would read ``clear`` and the screen
    would show a clean project. Nothing would raise.
    """
    _persist(_FakeSession())

    stored = [p for p in cv_registry if p.startswith("pkg:npm/%40")]
    assert stored, "persist must keep the encoded spelling cdxgen put in `purl`"
    assert not [p for p in cv_registry if p.startswith("pkg:npm/@")]
    assert cv_registry[FLAGGED_SCOPED].malicious_state == "flagged"


def test_rerun_is_idempotent(cv_registry: dict[str, _FakeComponentVersion]) -> None:
    _persist(_FakeSession())
    first = cv_registry[FLAGGED_SCOPED].malicious_evaluated_at
    assert first is not None

    _persist(_FakeSession())  # same catalog rows re-observed
    assert cv_registry[FLAGGED_SCOPED].malicious_evaluated_at == first


def test_disabled_leaves_every_column_null(
    cv_registry: dict[str, _FakeComponentVersion], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off means NOT ASSESSED, which is not the same as clean.

    The columns stay NULL rather than being written ``clear``, so surfaces can
    tell "we looked and found nothing" from "we never looked".
    """
    monkeypatch.setenv("MALICIOUS_ENABLED", "false")
    _persist(_FakeSession())

    assert all(cv.malicious_state is None for cv in cv_registry.values())
    assert all(cv.malicious_evaluated_at is None for cv in cv_registry.values())


def test_a_missing_version_field_cannot_clear_a_flagged_row(
    cv_registry: dict[str, _FakeComponentVersion],
) -> None:
    """An uploaded SBOM must not be able to un-flag a shared catalog row.

    ``component_versions`` is org-wide, so a wrong verdict here reaches every
    project using the package. The hook used to decide from the SBOM's
    ``version`` field, which defaults to "0.0.0" when absent — against a
    version-pinned advisory that reads as "not one of the named versions" and
    clears the row. 12.4% of the snapshot is version-pinned, so this was not a
    corner case. The verdict now comes from the PURL, which is the same string
    the row is keyed on.
    """
    from services.malicious import malicious_catalog

    index = malicious_catalog.load_index()
    assert index is not None
    pinned_purl, versions = next(
        (k, v) for k, v in index.versions.items() if k.startswith("pkg:")
    )
    named = versions[0]

    sbom = {
        "components": [
            # `purl` names the malicious release; `version` is absent, exactly
            # what an attacker-supplied document would look like.
            {"name": "pinned", "purl": f"{pinned_purl}@{named}", "type": "library"}
        ]
    }
    from tasks.scan_source import persist_sbom_components

    persist_sbom_components(_FakeSession(), scan_uuid=uuid.uuid4(), sbom=sbom)

    row = cv_registry[f"{pinned_purl}@{named}"]
    assert row.malicious_state == "flagged"


def test_flagging_never_creates_a_finding(
    cv_registry: dict[str, _FakeComponentVersion],
) -> None:
    """The central promise: this axis is not a vulnerability.

    A malicious package is removed and its reachable credentials rotated; a CVE
    is patched. Filing the flag as a finding would put it on the severity axis
    and tell the reader to schedule an upgrade — the wrong action. Persisting a
    flagged component must therefore add no VulnerabilityFinding row.
    """
    session = _FakeSession()
    _persist(session)

    assert cv_registry[FLAGGED_SCOPED].malicious_state == "flagged"
    added_types = {type(row).__name__ for row in session.added}
    assert "VulnerabilityFinding" not in added_types
