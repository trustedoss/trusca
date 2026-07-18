"""End-of-life (EOL) component flagging — Phase M.

Package layout:
  - ``eol_catalog.py`` — pure evaluation logic (rules → cycle → verdict).
  - ``eol_purl_map.json`` — the purl→product whitelist, vendored VERBATIM
    from BomLens ``docker/lib/eol-purl-map.json`` (consistency-contract
    tested against a captured copy, the G7 registry precedent).
  - ``eol_snapshot.json`` — compact endoflife.date dataset, vendored into
    the repo and refreshed per release via
    ``scripts/refresh_eol_snapshot.py`` (zero network at scan time).
"""

from services.eol.eol_catalog import (
    CURRENCY_STATES,
    EolDataset,
    EolRule,
    EolVerdict,
    build_evaluator,
    derive_cycle,
    evaluate,
    load_dataset,
    load_rules,
    stamp_component_version,
)

__all__ = [
    "CURRENCY_STATES",
    "EolDataset",
    "EolRule",
    "EolVerdict",
    "build_evaluator",
    "derive_cycle",
    "evaluate",
    "load_dataset",
    "load_rules",
    "stamp_component_version",
]
