# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""What a BomLens run says about its result, in the records TRUSCA already keeps.

The in-process pipeline writes three records that say how complete a scan's
result is, and the project Overview, the Components tab and the metrics read
them. A BomLens run reports the same three facts its own way; this module turns
each into the record TRUSCA readers already understand, so none of them needs
to know which engine ran.

``license_enrichment`` (licence lookups not made)
    BomLens' ``license-registry`` step asks deps.dev for missing licences and
    records per-component ``counts``. A component whose answer did not arrive
    within the step's time budget is ``budget_reached``; that is the in-process
    ``budget_exhausted``. BomLens has no circuit breaker, so ``breaker_open``
    never comes from it. ``lookup_failed`` is a lookup that was made and failed,
    which the in-process record counts as ``lookup_failures`` and not as "not
    looked up".

``scope_filter`` (what was left out as not shipped)
    BomLens leaves test, example and benchmark folders out before generation
    and writes the patterns (``bomlens:excluded-paths``) and the manifest files
    it skipped (``bomlens:excluded-manifests``) on the SBOM document. The
    in-process filter drops components after generation and counts them in
    ``dropped``. Nothing was dropped here, since those components were never
    generated, so ``dropped`` stays empty and the patterns and files are kept
    beside it. ``bomlens:*`` properties are not part of the engine contract, so
    anything missing or malformed is read as "nothing recorded", never as an
    error.

``component_outcome`` (a source that declares dependencies but yielded none)
    BomLens records ``generate`` as degraded with ``no_components`` and puts
    the manifests in ``bomlens:sbom-empty-with-manifests``. That is the
    in-process ``empty_with_manifests``.
"""

from __future__ import annotations

import re
from typing import Any, Final

from integrations.bomlens.contract import EngineResult
from services import scan_outcome

#: The BomLens step that asks the package registry for licences.
REGISTRY_STEP: Final = "license-registry"

#: ``counts`` keys BomLens' ``license-registry`` step writes (engine contract,
#: step table). Every key is either a lookup that was made or one that was not.
REGISTRY_COUNT_KEYS: Final = (
    "candidates",
    "filled",
    "superseded",
    "confirmed",
    "no_answer",
    "not_found",
    "non_standard",
    "lookup_failed",
    "budget_reached",
)

#: BomLens count key -> the reason TRUSCA records for lookups it did not make
#: (``scan_outcome.LICENSE_LOOKUP_REASONS``).
REGISTRY_NOT_LOOKED_UP: Final[dict[str, str]] = {"budget_reached": "budget_exhausted"}

#: BomLens count keys for lookups that were made and got no usable answer
#: because the request failed (the in-process ``lookup_failures``).
REGISTRY_LOOKUP_FAILURES: Final = ("lookup_failed",)

#: Where each record is kept in ``scan_metadata``.
SCOPE_FILTER_KEY: Final = "scope_filter"

#: SBOM document properties BomLens writes.
EXCLUDED_PATHS_PROPERTY: Final = "bomlens:excluded-paths"
EXCLUDED_MANIFESTS_PROPERTY: Final = "bomlens:excluded-manifests"
EMPTY_WITH_MANIFESTS_PROPERTY: Final = "bomlens:sbom-empty-with-manifests"

#: The ``generate`` reason BomLens records for the same thing.
NO_COMPONENTS_REASON: Final = "no_components"

#: Which engine wrote a record, kept on the record.
ENGINE_NAME: Final = "bomlens"

#: Bounds on what is copied from the SBOM into ``scan_metadata``. The file names
#: come from the scanned tree, so they are as untrusted as the in-process
#: filter's ``dropped_refs`` and get the same treatment: a count cap and a
#: per-entry cap.
MAX_LISTED: Final = 50
MAX_ENTRY_CHARS: Final = 200

_MORE_SUFFIX: Final = re.compile(r"\s*\(\+(\d+) more\)\s*$")


def _non_negative(counts: dict[str, int], key: str) -> int:
    value = counts.get(key, 0)
    return value if value > 0 else 0


def license_enrichment_record(result: EngineResult) -> dict[str, Any] | None:
    """The ``license_enrichment`` record for this run, or ``None``.

    ``None`` when the step did not run or every lookup was made: the
    in-process record is likewise written only when something was skipped, and
    the Overview notice and the metric read its absence as "nothing skipped".
    """
    step = result.step(REGISTRY_STEP)
    if step is None or step.status not in ("ok", "degraded"):
        return None
    counts = step.counts
    skipped_by_reason = {
        reason: _non_negative(counts, key) for key, reason in REGISTRY_NOT_LOOKED_UP.items()
    }
    not_looked_up = sum(skipped_by_reason.values())
    if not_looked_up <= 0:
        return None
    reasons = sorted(r for r, n in skipped_by_reason.items() if n > 0)
    reason = reasons[0] if len(reasons) == 1 else "both"
    spent = max((step.ended_at - step.started_at).total_seconds(), 0.0)
    return {
        # The engine's budget is its own setting; this reader does not see it.
        "budget_seconds": None,
        "spent_seconds": round(spent, 1),
        "lookups": max(_non_negative(counts, "candidates") - not_looked_up, 0),
        "lookup_failures": sum(_non_negative(counts, k) for k in REGISTRY_LOOKUP_FAILURES),
        "not_looked_up": not_looked_up,
        "not_looked_up_reason": reason,
        "engines": [ENGINE_NAME],
    }


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def merge_license_enrichment(existing: Any, engine: dict[str, Any] | None) -> dict[str, Any] | None:
    """One record from the in-process lookups (``existing``) and the engine's.

    Both can skip lookups in one scan: BomLens asks deps.dev inside the engine,
    and the persist path TRUSCA shares with the in-process pipeline asks the
    registries again for what is still unlicensed. The counts add up and the
    reason becomes ``both`` when the two differ, the value the in-process
    record already uses for budget and breaker together.
    """
    if engine is None:
        return existing if isinstance(existing, dict) else None
    if not isinstance(existing, dict) or _int(existing.get("not_looked_up")) == 0:
        return dict(engine)
    reasons = {existing.get("not_looked_up_reason"), engine["not_looked_up_reason"]}
    reason = reasons.pop() if len(reasons) == 1 else "both"
    if reason not in scan_outcome.LICENSE_LOOKUP_REASONS:
        reason = "both"
    return {
        "budget_seconds": existing.get("budget_seconds"),
        "spent_seconds": round(
            _number(existing.get("spent_seconds")) + _number(engine["spent_seconds"]), 1
        ),
        "lookups": _int(existing.get("lookups")) + engine["lookups"],
        "lookup_failures": _int(existing.get("lookup_failures")) + engine["lookup_failures"],
        "not_looked_up": _int(existing.get("not_looked_up")) + engine["not_looked_up"],
        "not_looked_up_reason": reason,
        "engines": ["inprocess", ENGINE_NAME],
    }


def _document_properties(sbom: dict[str, Any]) -> dict[str, str]:
    metadata = sbom.get("metadata")
    raw = metadata.get("properties") if isinstance(metadata, dict) else None
    found: dict[str, str] = {}
    if not isinstance(raw, list):
        return found
    for item in raw:
        if not isinstance(item, dict):
            continue
        name, value = item.get("name"), item.get("value")
        if isinstance(name, str) and isinstance(value, str) and name not in found:
            found[name] = value
    return found


def _listed(value: str) -> tuple[list[str], int]:
    """``"a, b (+3 more)"`` -> ``(["a", "b"], 3)``, bounded."""
    more = 0
    match = _MORE_SUFFIX.search(value)
    if match:
        more = int(match.group(1))
        value = value[: match.start()]
    entries = [entry.strip() for entry in value.split(",")]
    entries = [entry[:MAX_ENTRY_CHARS] for entry in entries if entry]
    if len(entries) > MAX_LISTED:
        more += len(entries) - MAX_LISTED
        entries = entries[:MAX_LISTED]
    return entries, more


def scope_filter_record(sbom: dict[str, Any], *, kept: int) -> dict[str, Any] | None:
    """The ``scope_filter`` record for an SBOM BomLens wrote, or ``None``.

    ``None`` when the SBOM names no excluded pattern: BomLens writes the
    patterns whenever its exclusion was on, so their absence means it was off.
    """
    properties = _document_properties(sbom)
    paths, _ = _listed(properties.get(EXCLUDED_PATHS_PROPERTY, ""))
    if not paths:
        return None
    manifests, more = _listed(properties.get(EXCLUDED_MANIFESTS_PROPERTY, ""))
    return {
        "applied": True,
        # Same keys as the in-process filter's record. Nothing was dropped
        # after generation: the excluded folders were never read.
        "dropped": {},
        "kept": kept,
        "dropped_refs": [],
        "excluded_paths": paths,
        "excluded_manifests": manifests,
        "excluded_manifests_more": more,
        "engine": ENGINE_NAME,
    }


def declared_but_empty(result: EngineResult, sbom: dict[str, Any]) -> bool:
    """Whether BomLens said the source declares dependencies it did not yield.

    Either signal is enough: the manifest's ``generate`` reason is the
    contract, the SBOM property is what the engine also writes, and a run that
    lost one of them is still the same finding.
    """
    if any(s.id == "generate" and s.reason == NO_COMPONENTS_REASON for s in result.steps):
        return True
    return bool(_document_properties(sbom).get(EMPTY_WITH_MANIFESTS_PROPERTY, "").strip())


def component_outcome(
    *,
    component_count: int,
    manifest_inventory: dict[str, Any] | None,
    engine_declared_but_empty: bool,
) -> str:
    """``scan_outcome.METADATA_KEY`` for a BomLens run.

    The engine's own finding wins when it says the source declared
    dependencies: it looked at the tree after leaving out the folders that do
    not ship, the same tree it scanned. Otherwise TRUSCA's own inventory
    decides, as for an in-process scan.
    """
    if component_count > 0:
        return scan_outcome.COMPONENTS_FOUND
    if engine_declared_but_empty:
        return scan_outcome.EMPTY_WITH_MANIFESTS
    return scan_outcome.classify_component_outcome(
        component_count=component_count, manifest_inventory=manifest_inventory
    )


__all__ = [
    "EMPTY_WITH_MANIFESTS_PROPERTY",
    "EXCLUDED_MANIFESTS_PROPERTY",
    "EXCLUDED_PATHS_PROPERTY",
    "REGISTRY_COUNT_KEYS",
    "REGISTRY_LOOKUP_FAILURES",
    "REGISTRY_NOT_LOOKED_UP",
    "REGISTRY_STEP",
    "SCOPE_FILTER_KEY",
    "component_outcome",
    "declared_but_empty",
    "license_enrichment_record",
    "merge_license_enrichment",
    "scope_filter_record",
]
