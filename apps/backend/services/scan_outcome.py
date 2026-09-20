# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Whether a scan that succeeded actually found anything.

A scan whose SBOM came back with zero components exits 0 and is recorded as
``succeeded``, because nothing on the path counts components: cdxgen's exit
code is the only signal read, and the load loop simply iterates an empty list
zero times. The project then shows no components, no licences and no CVEs, the
build gate passes because every count it reads is 0, and the Components tab
renders "No components yet / A scan is what fills this table", which reads as
"you have not scanned" to somebody who just did.

Making an empty result a failure would be wrong. cdxgen does not recognise
every build system, and for a tree it cannot read, zero components is the
correct and expected answer; failing there would break builds that are working
as intended. So the scan still succeeds and what changes is that it says which
kind of empty it was.

Three outcomes, and the distinction between the last two is the point:

``components_found``
    The ordinary case.

``empty_no_manifests``
    Zero components, and the tree declared no dependency manifest either.
    Nothing was there to find. Expected for an unsupported build system, and
    the honest thing to tell the user is that TRUSCA could not read their
    ecosystem, not that they have no dependencies.

``empty_with_manifests``
    Zero components even though the tree carried manifests. Something did go
    wrong: build-prep is best-effort and a prep failure leaves the lockfile
    cdxgen needed missing, which empties that ecosystem silently.

What this deliberately does NOT cover is under-reporting. A tree with a
``package.json`` and no ``package-lock.json`` yields the direct dependencies
and drops the transitive ones: measured, not assumed. That is a populated SBOM
that is quietly incomplete, and it has a different cause and a different thing
for the user to do about it, so folding it into the same warning would tell
half of them to take an action that does not apply.
"""

from __future__ import annotations

from typing import Any, Final

#: Ordinary result: the SBOM carries components.
COMPONENTS_FOUND: Final = "components_found"

#: Zero components and no manifests either. Nothing was there to find.
EMPTY_NO_MANIFESTS: Final = "empty_no_manifests"

#: Zero components despite the tree declaring manifests. Something failed.
EMPTY_WITH_MANIFESTS: Final = "empty_with_manifests"

#: The key this verdict is stored under in ``scans.scan_metadata``.
METADATA_KEY: Final = "component_outcome"

#: Where a scan records licence lookups it did not make (time budget spent or
#: circuit breaker open). Absent when every lookup was made. Kept beside the
#: component outcome because it answers the same question, "is this result
#: complete", for a different part of the result.
LICENSE_ENRICHMENT_KEY: Final = "license_enrichment"

#: Where a scan records that first-party licence detection (scancode) did not
#: run for a reason the operator did not choose. Absent when the stage ran, and
#: also absent when it was turned off on purpose (``SCANCODE_ENABLED=false``).
SCANCODE_SKIP_KEY: Final = "scancode_skipped"

SCANCODE_SKIP_REASONS: Final = ("not_installed", "failed", "timeout", "too_large")

LICENSE_LOOKUP_REASONS: Final = ("budget_exhausted", "breaker_open", "both")

COMPONENT_OUTCOME_VALUES: Final = (
    COMPONENTS_FOUND,
    EMPTY_NO_MANIFESTS,
    EMPTY_WITH_MANIFESTS,
)


def manifest_count(inventory: dict[str, Any] | None) -> int:
    """How many dependency manifests the fetched tree carried.

    ``collect_manifest_inventory`` returns ``None`` both when it found nothing
    and when it could not look, and those are different: "no manifests" is a
    finding, "could not look" is not. Callers that need to tell them apart
    check the inventory for ``None`` themselves; this only counts.
    """
    if not inventory:
        return 0
    count = inventory.get("count")
    return int(count) if isinstance(count, int) else 0


def classify_component_outcome(
    *,
    component_count: int,
    manifest_inventory: dict[str, Any] | None,
) -> str:
    """Classify a finished source scan by what its SBOM ended up containing.

    ``manifest_inventory`` is the value ``collect_manifest_inventory`` returned
    for the same scan, taken BEFORE build-prep ran, so it describes what the
    source declared rather than what we generated from it.
    """
    if component_count > 0:
        return COMPONENTS_FOUND
    if manifest_count(manifest_inventory) > 0:
        return EMPTY_WITH_MANIFESTS
    return EMPTY_NO_MANIFESTS


def scancode_skip_reason(metadata: dict[str, Any] | None) -> str | None:
    """The recorded reason scancode was skipped, or ``None`` when it was not.

    An unrecognised stored value reads as ``None``: the notice is only drawn
    for a reason we can describe.
    """
    reason = (metadata or {}).get(SCANCODE_SKIP_KEY)
    return reason if reason in SCANCODE_SKIP_REASONS else None


def license_lookup_gap(metadata: dict[str, Any] | None) -> tuple[int, str] | None:
    """``(not_looked_up, reason)`` from the licence enrichment record, or ``None``.

    ``None`` when the record is absent (every lookup was made) or malformed.
    A zero count is also ``None``: nothing was skipped, so nothing to show.
    """
    record = (metadata or {}).get(LICENSE_ENRICHMENT_KEY)
    if not isinstance(record, dict):
        return None
    skipped = record.get("not_looked_up")
    reason = record.get("not_looked_up_reason")
    if (
        not isinstance(skipped, int)
        or isinstance(skipped, bool)
        or skipped <= 0
        or reason not in LICENSE_LOOKUP_REASONS
    ):
        return None
    return skipped, str(reason)


def is_empty(outcome: str | None) -> bool:
    """True when the outcome says the scan produced no components."""
    return outcome in (EMPTY_NO_MANIFESTS, EMPTY_WITH_MANIFESTS)


__all__ = [
    "COMPONENTS_FOUND",
    "COMPONENT_OUTCOME_VALUES",
    "EMPTY_NO_MANIFESTS",
    "EMPTY_WITH_MANIFESTS",
    "LICENSE_ENRICHMENT_KEY",
    "LICENSE_LOOKUP_REASONS",
    "METADATA_KEY",
    "SCANCODE_SKIP_KEY",
    "SCANCODE_SKIP_REASONS",
    "classify_component_outcome",
    "is_empty",
    "license_lookup_gap",
    "manifest_count",
    "scancode_skip_reason",
]
