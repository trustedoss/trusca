#!/usr/bin/env python3
"""
refresh_eol_snapshot.py — rebuild the vendored endoflife.date snapshot.

Maintainer-run, once per release (and whenever ``services/eol/eol_purl_map.json``
gains a product)::

    python3 scripts/refresh_eol_snapshot.py

Port of BomLens ``docker/build-eol-index.py`` with one structural change:
BomLens bakes the snapshot at Docker BUILD time, TRUSCA vendors it into the
repo — Docker builds stay network-free/reproducible and air-gapped installs
get a working dataset out of the box. Scan-time is offline either way.

Fetches https://endoflife.date/api/{product}.json for exactly the products
the map references, keeps only the fields the evaluator reads, and writes a
compact JSON (a few KB) with the build date under ``_snapshot``.

Attribution: end-of-life dates are sourced from https://endoflife.date
(code MIT; the lifecycle dates are factual data). The snapshot date is
surfaced per stamped row via ``component_versions.eol_source``.

Best-effort: a product whose fetch fails is skipped with a warning; if
NOTHING is fetched the existing vendored file is left untouched (EOL
flagging keeps using the previous snapshot).
"""

from __future__ import annotations

import datetime
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://endoflife.date/api/{}.json"
# Only the fields the evaluator (and the M-3 staleness surface) reads.
KEEP = ("cycle", "eol", "releaseDate", "latest", "latestReleaseDate")
# Bound the whole run so a black-holed network cannot stall a release build:
# per-request cap + total budget; products past the deadline are skipped.
REQUEST_TIMEOUT = 15
TOTAL_BUDGET = 60

EOL_DIR = Path(__file__).resolve().parent.parent / "services" / "eol"
MAP_PATH = EOL_DIR / "eol_purl_map.json"
OUT_PATH = EOL_DIR / "eol_snapshot.json"


def distinct_products(map_path: Path) -> list[str]:
    data = json.loads(map_path.read_text(encoding="utf-8"))
    seen: list[str] = []
    for rule in data.get("rules", []):
        product = rule.get("product")
        if product and product not in seen:
            seen.append(product)
    return seen


def fetch(product: str) -> list[dict[str, object]]:
    # S310: scheme is fixed https on a constant host (API template above) —
    # product slugs come from the vendored map, not user input.
    url = API.format(product)
    request = urllib.request.Request(  # noqa: S310
        url, headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
        cycles = json.load(response)
    return [{k: c[k] for k in KEEP if k in c} for c in cycles]


def main() -> int:
    products = distinct_products(MAP_PATH)
    out: dict[str, object] = {"_snapshot": datetime.date.today().isoformat()}
    ok = 0
    failed: list[str] = []
    skipped: list[str] = []
    deadline = time.monotonic() + TOTAL_BUDGET
    for product in products:
        if time.monotonic() > deadline:
            skipped.append(product)
            continue
        try:
            out[product] = fetch(product)
            ok += 1
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            failed.append(product)
            sys.stderr.write(f"[eol-snapshot] WARN: could not fetch {product}: {exc}\n")
    if skipped:
        sys.stderr.write(
            f"[eol-snapshot] WARN: time budget ({TOTAL_BUDGET}s) spent; "
            f"skipped {len(skipped)}: {skipped}\n"
        )

    if ok == 0:
        sys.stderr.write(
            "[eol-snapshot] WARN: fetched 0 products; keeping the existing "
            "vendored snapshot untouched.\n"
        )
        return 0
    if failed:
        sys.stderr.write(
            f"[eol-snapshot] WARN: {len(failed)} product(s) failed and will be "
            f"missing from the snapshot: {failed} — components mapping to them "
            "will evaluate to 'unknown'. Re-run to fill them in.\n"
        )

    OUT_PATH.write_text(
        json.dumps(out, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sys.stderr.write(
        f"[eol-snapshot] bundled {ok} product(s) into {OUT_PATH} "
        f"(snapshot {out['_snapshot']}); {len(failed)} failed\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
