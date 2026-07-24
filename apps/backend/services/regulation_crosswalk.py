"""
Regulatory crosswalk for SBOM conformance checks — join + per-framework rollup.

Vendored data: ``regulation_crosswalk.json`` is a VERBATIM copy of the SK
Telecom BomLens ``docker/lib/regulation-crosswalk.json`` (v1.8.3 line, PR
sktelecom/sbom-tools#462). Refresh procedure: re-copy the upstream file and
diff — a contract test pins the structural invariants (map keys resolve to
known check ids, every mapped framework is declared) so an upstream rename
fails loudly here instead of silently dropping references.

This is a faithful Python port of two jq passes in BomLens
``validate-sbom.sh``:

  join   : ``join_crosswalk`` — every check whose id appears in the crosswalk
           ``map`` gains ``regulations: [{framework, ref, basis, short,
           short_ko}]``; unmapped checks gain ``regulations: []``.
  rollup : ``XW_SUMMARY`` — one row per framework that has at least one mapped
           check in this result: ``{id, title, …, total, present, gap, review,
           elements[]}`` where present = pass, gap = warn with an automated
           source, review = source "na" (human-review-only). A failed
           mandatory check counts in ``total`` only — same as upstream: a
           mandatory failure already fails the whole submission, the crosswalk
           is not a second verdict.

Both passes are purely informational: they NEVER change a check status, the
counters, or the overall result (the disclaimer in the vendored file is part
of the wire payload for exactly that reason). The crosswalk answers one
question for a reviewer — "when a check does not pass, which regulatory
obligation does that gap touch?" — it does not determine compliance.

Pure functions over plain dicts (the ``checks`` JSONB shape), no DB / network.
The vendored file is a static repo asset, so module-level caching is safe —
this is data, not environment configuration (CLAUDE.md §11 applies to env
vars, not vendored catalogues).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CROSSWALK_PATH = Path(__file__).with_name("regulation_crosswalk.json")

# Crosswalk map keys that intentionally have NO corresponding TRUSCA check.
# BomLens emits ``spec-version`` / ``purl-syntax`` checks; TRUSCA's ingest
# accepts CycloneDX 1.7 ML-BOMs (a fixed accepted-version list would fail
# them) and PURL well-formedness is enforced by the matcher itself. The
# contract test allows exactly this set to stay unreferenced — anything else
# unknown means an upstream drift that needs a port decision.
UNPORTED_CHECK_IDS: frozenset[str] = frozenset({"spec-version", "purl-syntax"})


@lru_cache(maxsize=1)
def _crosswalk() -> dict[str, Any]:
    with _CROSSWALK_PATH.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    return loaded if isinstance(loaded, dict) else {}


def attach_regulations(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return new check dicts with ``regulations`` joined onto each by id.

    Mirrors the BomLens jq join: ``regulations = (map[.id] // []) | map(. +
    {short, short_ko})``. Input dicts are not mutated (the caller may hold the
    raw JSONB row).
    """
    xwalk = _crosswalk()
    mapping = xwalk.get("map") or {}
    frameworks = xwalk.get("frameworks") or {}

    joined: list[dict[str, Any]] = []
    for check in checks:
        refs = []
        for entry in mapping.get(check.get("id"), []):
            meta = frameworks.get(entry.get("framework"), {})
            refs.append(
                {
                    **entry,
                    "short": meta.get("short") or entry.get("framework"),
                    "short_ko": meta.get("short_ko")
                    or meta.get("short")
                    or entry.get("framework"),
                }
            )
        joined.append({**check, "regulations": refs})
    return joined


def crosswalk_summary(joined_checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-framework rollup over checks that already carry ``regulations``.

    Mirrors BomLens ``XW_SUMMARY``: frameworks appear in vendored-file order,
    only when at least one check maps to them. ``title_ko`` / ``short_ko``
    ride along (the API is one payload for both locales; BomLens swaps them
    at report-render time instead).
    """
    xwalk = _crosswalk()
    frameworks = xwalk.get("frameworks") or {}
    mapped_rows = [c for c in joined_checks if c.get("regulations")]

    out_frameworks: list[dict[str, Any]] = []
    for fid, meta in frameworks.items():
        rows = [
            c
            for c in mapped_rows
            if any(r.get("framework") == fid for r in c["regulations"])
        ]
        if not rows:
            continue
        out_frameworks.append(
            {
                "id": fid,
                "title": meta.get("title") or fid,
                "title_ko": meta.get("title_ko") or meta.get("title") or fid,
                "short": meta.get("short") or fid,
                "short_ko": meta.get("short_ko") or meta.get("short") or fid,
                "source": meta.get("source") or "",
                "total": len(rows),
                "present": sum(1 for c in rows if c.get("status") == "pass"),
                "gap": sum(
                    1
                    for c in rows
                    if c.get("status") == "warn" and (c.get("source") or "") != "na"
                ),
                "review": sum(1 for c in rows if (c.get("source") or "") == "na"),
                "elements": [
                    {
                        "id": c.get("id"),
                        "label": c.get("label"),
                        "status": c.get("status"),
                        "source": c.get("source"),
                        "detail": c.get("detail"),
                        "refs": [
                            r.get("ref")
                            for r in c["regulations"]
                            if r.get("framework") == fid
                        ],
                    }
                    for c in rows
                ],
            }
        )

    return {
        "disclaimer": xwalk.get("disclaimer") or "",
        "disclaimer_ko": xwalk.get("disclaimer_ko") or xwalk.get("disclaimer") or "",
        "frameworks": out_frameworks,
    }
