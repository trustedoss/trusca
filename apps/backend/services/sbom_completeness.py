# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""What an exported SBOM may say about its own completeness (U3-C).

CycloneDX ``compositions`` lets a document state, per set of components or
dependency entries, whether that set is ``complete`` ("no further ... are known
to exist"), ``incomplete``, or ``unknown``. Before this an export said nothing,
which a reader has to take as "complete", and a scan that lost its build
preparation looks the same as one that did not.

The claim is deliberately narrow. ``complete`` here means no failure we
recorded could have dropped a component or an edge, not that the SBOM lists
everything the software contains: a manifest without its lockfile still yields
direct dependencies only, and that is invisible to this record. What the
verdict can be based on is what the scan recorded about itself, so:

* A scan that predates the stage record (no ``stage_timings``) is ``unknown``,
  because an absent ``stage_outcomes`` then means "not recorded", not "nothing
  degraded".
* A document TRUSCA did not generate (an ingested SBOM) is ``unknown``.
* A stage that can lose dependency information and reported a failure makes the
  set ``incomplete``. Stages that can only add (the scope filter) or that do not
  touch the inventory (signing, licenses) are not counted.
* An empty result with manifests present is ``incomplete``; without manifests it
  is ``unknown`` (an ecosystem the scanner cannot read).
* Components removed by the ``filtered`` export profile make that export
  ``incomplete`` on purpose.

Kept apart from ``sbom_export`` so the rules can be tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from services import scan_outcome

COMPLETE: Final = "complete"
INCOMPLETE: Final = "incomplete"
UNKNOWN: Final = "unknown"

#: Stages whose failure can leave a component or a dependency edge out.
INVENTORY_STAGES: Final = ("prep", "cocoapods")

#: Document property naming why the verdict is what it is.
BASIS_PROPERTY: Final = "trusca:composition-basis"


@dataclass(frozen=True)
class Completeness:
    """The verdict for the component set and for the dependency graph."""

    assemblies: str
    dependencies: str
    basis: str


def _inventory_verdict(
    *,
    scan_kind: str | None,
    metadata: dict[str, Any] | None,
    excluded_count: int,
) -> tuple[str, str]:
    meta = metadata or {}
    if scan_kind == "sbom":
        return UNKNOWN, "ingested_document"
    if scan_outcome.STAGE_TIMINGS_KEY not in meta:
        return UNKNOWN, "not_recorded"
    if excluded_count > 0:
        return INCOMPLETE, "profile_filter"
    outcome = meta.get(scan_outcome.METADATA_KEY)
    if outcome == scan_outcome.EMPTY_WITH_MANIFESTS:
        return INCOMPLETE, "empty_with_manifests"
    if outcome == scan_outcome.EMPTY_NO_MANIFESTS:
        return UNKNOWN, "no_manifests"
    degraded = {entry["stage"] for entry in scan_outcome.degraded_stages(meta)}
    for stage in INVENTORY_STAGES:
        if stage in degraded:
            return INCOMPLETE, f"stage_degraded:{stage}"
    return COMPLETE, "no_known_gap"


def assess(
    *,
    scan_kind: str | None,
    metadata: dict[str, Any] | None,
    component_count: int,
    edge_count: int,
    excluded_count: int = 0,
) -> Completeness:
    """Verdicts for one exported scan."""
    inventory, basis = _inventory_verdict(
        scan_kind=scan_kind, metadata=metadata, excluded_count=excluded_count
    )
    if inventory != COMPLETE:
        return Completeness(assemblies=inventory, dependencies=inventory, basis=basis)
    if component_count > 1 and edge_count == 0:
        # The components were listed but no graph came with them (cdxgen gave no
        # ``dependencies``). Saying "complete" about edges that were never
        # produced would be the exact claim this module exists to avoid.
        return Completeness(assemblies=COMPLETE, dependencies=UNKNOWN, basis="no_graph")
    return Completeness(assemblies=COMPLETE, dependencies=COMPLETE, basis=basis)
