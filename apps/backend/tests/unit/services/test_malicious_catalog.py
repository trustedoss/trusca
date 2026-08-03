# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Malicious-package evaluation (#26) — unit tests.

The tests that matter most here are the PURL-shape ones. This feature fails
SILENTLY when the key spaces drift: every lookup misses, every component reads
``clear``, and the screen shows a clean project. Nothing raises. So the
encoding contract is pinned against the real vendored snapshot rather than a
hand-made fixture, per the hardening rule about persist-boundary fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from services.malicious import malicious_catalog


@dataclass
class _Row:
    """Stand-in for the ComponentVersion columns the stamper writes."""

    malicious_state: str | None = None
    malicious_id: str | None = None
    malicious_source: str | None = None
    malicious_evaluated_at: datetime | None = None


# ---------------------------------------------------------------------------
# base_purl
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("purl", "expected"),
    [
        ("pkg:npm/lodash@4.17.21", "pkg:npm/lodash"),
        # The encoded scope must survive: %40 is not a literal '@', so it is
        # not a version delimiter. Stripping or decoding it here is the exact
        # bug that would un-match every scoped package.
        ("pkg:npm/%40babel/core@7.29.7", "pkg:npm/%40babel/core"),
        ("pkg:npm/%40ctrl/tinycolor@4.1.1", "pkg:npm/%40ctrl/tinycolor"),
        # Qualifiers and subpath are cut before the version search.
        ("pkg:maven/g/a@1.0?type=jar", "pkg:maven/g/a"),
        ("pkg:golang/rsc.io/quote@v1.5.2#sub", "pkg:golang/rsc.io/quote"),
        # No version at all — already a base purl.
        ("pkg:pypi/requests", "pkg:pypi/requests"),
        # An unencoded leading scope marker is not a version separator either.
        ("pkg:npm/@scope/name", "pkg:npm/@scope/name"),
    ],
)
def test_base_purl_strips_version_but_never_the_scope(purl: str, expected: str) -> None:
    assert malicious_catalog.base_purl(purl) == expected


# ---------------------------------------------------------------------------
# Encoding contract against the vendored snapshot
# ---------------------------------------------------------------------------


def test_snapshot_spells_scoped_npm_packages_encoded() -> None:
    """The snapshot's key space must stay URL-encoded for scoped npm names.

    This is the measurement the design rests on (2026-08-03): OSV emits
    ``pkg:npm/%40ctrl/tinycolor`` and cdxgen's ``components[].purl`` — the
    field the persist hook stores — spells it the same way, so the two line up
    with no normalisation. If a future snapshot switched to ``@``, matching
    would silently drop every scoped package, so the shape is pinned here
    rather than left to a comment.
    """
    index = malicious_catalog.load_index()
    assert index is not None, "vendored snapshot must load"

    encoded = [k for k in index.packages if k.startswith("pkg:npm/%40")]
    unencoded = [k for k in index.packages if k.startswith("pkg:npm/@")]

    assert encoded, "expected scoped npm entries in the snapshot"
    assert not unencoded, (
        "snapshot switched to unencoded scopes — the matcher and the persist "
        f"hook now disagree. Examples: {unencoded[:3]}"
    )


def test_snapshot_column_widths_fit_the_schema() -> None:
    """Advisory ids and purls must fit the columns 0045 declared."""
    index = malicious_catalog.load_index()
    assert index is not None

    assert max(len(v) for v in index.packages.values()) <= 32  # malicious_id
    assert max(len(k) for k in index.packages) <= 255  # purl lookups


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def test_flags_a_known_malicious_scoped_package() -> None:
    """The case the encoding contract exists for, end to end."""
    evaluator = malicious_catalog.build_evaluator()
    assert evaluator is not None

    verdict = evaluator.verdict_for("pkg:npm/%40ctrl/tinycolor@4.1.1", "4.1.1")

    assert verdict.state == "flagged"
    assert verdict.advisory_id is not None
    assert verdict.advisory_id.startswith("MAL-")
    # The claim is bounded by the snapshot it came from, never open-ended.
    assert verdict.source.startswith("osv.dev@")


def test_an_ordinary_package_reads_clear_not_unknown() -> None:
    """``clear`` is a real verdict here — the index is a deny list.

    Contrast EOL, whose whitelist leaves unmapped components at NULL. Here
    "not in the index" means the snapshot looked and did not list it, which is
    information worth persisting.
    """
    evaluator = malicious_catalog.build_evaluator()
    assert evaluator is not None

    verdict = evaluator.verdict_for("pkg:npm/%40babel/core@7.29.7", "7.29.7")

    assert verdict.state == "clear"
    assert verdict.advisory_id is None


def test_version_pinned_advisories_only_flag_the_named_versions() -> None:
    """12.4% of entries name versions; the rest mean "every version".

    A package whose advisory names two bad releases must not condemn the
    releases published before the compromise.
    """
    index = malicious_catalog.load_index()
    assert index is not None
    evaluator = malicious_catalog.build_evaluator()
    assert evaluator is not None

    pinned_purl, versions = next(iter(index.versions.items()))
    named = versions[0]

    assert evaluator.verdict_for(f"{pinned_purl}@{named}", named).state == "flagged"
    assert (
        evaluator.verdict_for(
            f"{pinned_purl}@0.0.0-not-a-real-release", "0.0.0-not-a-real-release"
        ).state
        == "clear"
    )


def test_a_fixed_release_boundary_clears_later_versions() -> None:
    """The regression that made this boundary necessary.

    ``MAL-2023-462`` marks fsevents malicious from 1.0.0 and fixed in 1.2.11.
    The upstream builder keeps only explicit ``versions`` lists and drops
    ``ranges``, which collapses "fixed in 1.2.11" into "every version is
    malicious" — and fsevents at 2.x is in most Node projects, including this
    repo's own scan fixture, where it fired the first time the matcher ran.

    Only 23 npm advisories carry a fix boundary, which is exactly why it is
    pinned: a case this rare will not be caught by chance a second time.
    """
    evaluator = malicious_catalog.build_evaluator()
    assert evaluator is not None

    # Inside the compromised range.
    assert evaluator.verdict_for("pkg:npm/fsevents@1.2.10", "1.2.10").state == "flagged"
    # The fixed release itself, and anything after it, is clean.
    assert evaluator.verdict_for("pkg:npm/fsevents@1.2.11", "1.2.11").state == "clear"
    assert evaluator.verdict_for("pkg:npm/fsevents@2.3.3", "2.3.3").state == "clear"


def test_the_snapshot_carries_its_fix_boundaries() -> None:
    """A rebuild that dropped ``fixed_before`` would silently reintroduce the
    false positives, since every lookup would simply flag more."""
    index = malicious_catalog.load_index()
    assert index is not None
    assert index.fixed_before, "snapshot lost its fix boundaries"
    assert "pkg:npm/fsevents" in index.fixed_before


# ---------------------------------------------------------------------------
# Stamping
# ---------------------------------------------------------------------------


def test_stamp_writes_then_stays_idempotent() -> None:
    row = _Row()
    now = datetime.now(UTC)
    verdict = malicious_catalog.MaliciousVerdict(
        "flagged", "MAL-2025-47141", "osv.dev@2026-08-03"
    )

    assert malicious_catalog.stamp_component_version(row, verdict, now) is True
    assert row.malicious_state == "flagged"
    assert row.malicious_id == "MAL-2025-47141"
    assert row.malicious_evaluated_at == now

    # Re-scan / weekly re-stamp must not dirty an unchanged row.
    later = datetime.now(UTC)
    assert malicious_catalog.stamp_component_version(row, verdict, later) is False
    assert row.malicious_evaluated_at == now


def test_stamp_reverts_a_withdrawn_advisory_to_clear() -> None:
    """The guard is direction-agnostic, which is the false-positive path.

    A wrongly-flagged package is challenged upstream; when the advisory is
    retracted the next snapshot drops it, and the re-stamp pass has to be able
    to walk the row back with no special case.
    """
    row = _Row(
        malicious_state="flagged",
        malicious_id="MAL-2025-47141",
        malicious_source="osv.dev@2026-08-03",
    )
    cleared = malicious_catalog.MaliciousVerdict("clear", None, "osv.dev@2026-09-01")

    assert (
        malicious_catalog.stamp_component_version(row, cleared, datetime.now(UTC))
        is True
    )
    assert row.malicious_state == "clear"
    assert row.malicious_id is None


def test_missing_snapshot_leaves_the_row_untouched() -> None:
    """``None`` means NOT ASSESSED — never clear an existing flag.

    Losing the snapshot (bad deploy, disabled feature) must not silently
    un-flag a package that is still malicious.
    """
    row = _Row(malicious_state="flagged", malicious_id="MAL-2025-47141")

    assert (
        malicious_catalog.stamp_component_version(row, None, datetime.now(UTC)) is False
    )
    assert row.malicious_state == "flagged"
