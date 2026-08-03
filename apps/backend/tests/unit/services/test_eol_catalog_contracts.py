"""Consistency-contract tests — vendored EOL purl map (Phase M).

The G7 registry precedent (CLAUDE.md hardening rule 2): the same vocabulary
living in two places needs a set-equality contract, or the copies drift while
each side's own tests stay green. Three copies exist here:

  1. ``services/eol/eol_purl_map.json`` — the map the evaluator runs on,
     vendored VERBATIM from BomLens ``docker/lib/eol-purl-map.json``;
  2. ``tests/fixtures/eol/bomlens-eol-purl-map.json`` — the captured BomLens
     original (byte-comparison anchor; refresh it when re-vendoring);
  3. ``services/eol/eol_snapshot.json`` — the dataset, whose product set the
     map's own ``_comment`` demands stays in sync with the rules.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from services.eol.eol_catalog import load_rules

_BACKEND = Path(__file__).resolve().parents[3]
VENDORED_MAP = _BACKEND / "services" / "eol" / "eol_purl_map.json"
CAPTURED_MAP = _BACKEND / "tests" / "fixtures" / "eol" / "bomlens-eol-purl-map.json"
VENDORED_SNAPSHOT = _BACKEND / "services" / "eol" / "eol_snapshot.json"


def test_vendored_map_is_byte_identical_to_the_captured_bomlens_copy() -> None:
    # Verbatim vendoring is the contract: the %40 npm-scope divergence is
    # absorbed in the MATCHER (eol_catalog.evaluate), never in the map, so
    # re-vendoring stays a plain file copy.
    assert VENDORED_MAP.read_bytes() == CAPTURED_MAP.read_bytes()


def test_map_products_and_snapshot_products_are_set_equal() -> None:
    map_products = {
        rule["product"]
        for rule in json.loads(VENDORED_MAP.read_text(encoding="utf-8"))["rules"]
    }
    snapshot = json.loads(VENDORED_SNAPSHOT.read_text(encoding="utf-8"))
    snapshot_products = {k for k in snapshot if not k.startswith("_")}
    assert map_products == snapshot_products, (
        "eol_purl_map.json rules and eol_snapshot.json products drifted — "
        "run scripts/refresh_eol_snapshot.py after changing the map"
    )


def test_snapshot_stamp_is_an_iso_date() -> None:
    snapshot = json.loads(VENDORED_SNAPSHOT.read_text(encoding="utf-8"))
    date.fromisoformat(snapshot["_snapshot"])  # raises on drift


def test_loader_accepts_every_vendored_rule() -> None:
    # load_rules drops malformed entries silently; the vendored map must
    # survive intact (a dropped rule would silently stop flagging a product).
    raw_rules = json.loads(VENDORED_MAP.read_text(encoding="utf-8"))["rules"]
    assert len(load_rules()) == len(raw_rules)


def test_every_snapshot_cycle_entry_is_evaluable() -> None:
    # Every entry must carry a cycle, and its eol value must be one of the
    # shapes evaluate() understands (bool / ISO date string / absent).
    snapshot = json.loads(VENDORED_SNAPSHOT.read_text(encoding="utf-8"))
    for product, cycles in snapshot.items():
        if product.startswith("_"):
            continue
        for entry in cycles:
            assert "cycle" in entry, f"{product}: entry without cycle"
            eol_value = entry.get("eol")
            if isinstance(eol_value, str):
                date.fromisoformat(eol_value)
            else:
                assert eol_value is None or isinstance(eol_value, bool)
