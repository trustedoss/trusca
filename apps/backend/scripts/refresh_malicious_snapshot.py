#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
refresh_malicious_snapshot.py — rebuild the vendored malicious-package snapshot.

Maintainer-run, once per release (beside ``refresh_eol_snapshot.py`` in the
release checklist)::

    python3 scripts/refresh_malicious_snapshot.py

What this data is, and why it is not another vulnerability feed
---------------------------------------------------------------
A CVE says an honest package has a flaw you can patch. A malicious package was
published to attack whoever installs it — typosquats, hijacked maintainer
accounts, install-time payloads. The response is removal plus rotation of every
credential the build could reach, not an upgrade. So the signal is kept off the
severity axis entirely (see ``services/malicious/malicious_catalog.py``).

Source: OSV's per-ecosystem bulk archives. Malicious advisories carry a ``MAL-``
id, which is how they are told apart from ordinary advisories in the same
archive. The records are published by the OpenSSF Package Analysis project
(``ossf/malicious-packages``, Apache-2.0) — attribution lives in
``THIRD_PARTY_NOTICES.md``.

Port of BomLens ``docker/build-malicious-index.py`` with four changes, all
deliberate:

1. **Vendored, not baked at image build.** BomLens bakes the index into its
   Docker image; TRUSCA vendors it into the repo, the same call EOL made — our
   Docker builds stay network-free and air-gapped installs work out of the box.
2. **Streaming download.** The original reads each archive into memory with
   ``resp.read()``; npm alone is ~204 MB. This streams to a temporary file so
   peak memory does not track archive size.
3. **``withdrawn`` filter.** Advisories the source has retracted are dropped.
   This is the wide end of the false-positive path: a wrongly-flagged package
   is challenged upstream, and the retraction reaches users through the next
   snapshot. A NuGet sample showed zero withdrawn entries, but the check costs
   nothing and the alternative is shipping a flag the source has disowned.
4. **Hygiene floor + growth log.** A partial fetch must never overwrite a good
   snapshot with half of one, so a result below ``HYGIENE_FLOOR`` of the
   previous PURL count aborts without writing. The growth line exists because
   the floor only catches collapses: measured 2026-07-28 → 2026-08-03, the
   index grew 232,687 → 233,093, about 0.2%/week. A run that reports growth far
   outside that band is worth a look even when it clears the floor.

Failure posture matches the EOL builder: an ecosystem that fails is skipped
with a warning; if NOTHING is fetched the vendored file is left untouched and
flagging keeps using the previous snapshot.
"""

from __future__ import annotations

import datetime
import json
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ARCHIVE = "https://osv-vulnerabilities.storage.googleapis.com/{}/all.zip"

# The ecosystems a TRUSCA SBOM can actually carry a PURL for, cheapest first so
# a spent time budget costs the least coverage. npm is last because it is
# larger than the other seven combined (~204 MB) — and it is also where the
# advisories are: 217,171 of 233,093 PURLs at the 2026-08-03 measurement.
ECOSYSTEMS = (
    "NuGet",
    "crates.io",
    "RubyGems",
    "Maven",
    "Packagist",
    "Go",
    "PyPI",
    "npm",
)

# A single archive can be ~204 MB, so the per-request cap is far larger than
# the EOL builder's; the total budget still bounds the whole run.
REQUEST_TIMEOUT = 300
TOTAL_BUDGET = 900

# Cap on the versions stored per PURL. Advisories that name no versions mean
# every published version is malicious, which is the common case (87.5% at the
# 2026-08-03 measurement) and needs no list at all.
MAX_VERSIONS_PER_PURL = 200

# Refuse to write a snapshot smaller than this fraction of the previous one.
# Guards the "some ecosystems 404'd and we shipped the remainder" accident.
HYGIENE_FLOOR = 0.5

MALICIOUS_DIR = Path(__file__).resolve().parent.parent / "services" / "malicious"
OUT_PATH = MALICIOUS_DIR / "malicious_snapshot.json"


def _version_key(value: str) -> tuple[int, ...]:
    """Numeric segments of a version, for ordering boundaries.

    Deliberately crude: it only ever compares release boundaries from the same
    package, and a non-numeric segment (pre-release tag, build metadata) stops
    the parse rather than guessing an ordering. Ties fall back to keeping
    whichever boundary was seen first, which stays on the conservative side.
    """
    parts: list[int] = []
    for segment in value.split("."):
        digits = ""
        for ch in segment:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def fetch_ecosystem(
    name: str,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str]]:
    """Return ``{purl: mal_id}``, ``{purl: [versions]}`` and ``{purl: fixed}``.

    Three shapes of advisory, and they do not mean the same thing:

    1. ``versions`` listed explicitly — only those versions are malicious.
    2. ``ranges`` with no ``fixed`` event — the package is malicious from some
       point onward with no clean release after it, so every version we could
       meet is malicious. Storing the open range would grow the index without
       changing a verdict.
    3. ``ranges`` WITH a ``fixed`` event — malicious up to that release and
       clean from it on. These must keep their boundary.

    Case 3 is rare (23 of 216,850 npm advisories at the 2026-08-03
    measurement, 0.01%) and the BomLens original collapses it into case 2,
    which flags every later version too. Rarity is not harmlessness: one of
    those 23 is ``fsevents``, fixed in 1.2.11 and present in most Node
    projects at 2.x. Treating it as wholly malicious flags a healthy
    dependency in nearly every macOS Node build — it fired on this repo's own
    scan fixture the first time the matcher ran.

    The PURL is taken verbatim from ``affected[].package.purl`` — no
    normalisation. OSV spells scoped npm packages URL-encoded
    (``pkg:npm/%40ctrl/tinycolor``) and so does cdxgen's ``purl`` field, so the
    two key spaces already line up. Normalising either side would break the
    match for every scoped package; see the plan's §2.4 for the measurement.
    """
    url = ARCHIVE.format(name)
    req = urllib.request.Request(  # noqa: S310 — constant https base, ARCHIVE above
        url, headers={"Accept": "application/zip"}
    )
    ids: dict[str, str] = {}
    versions: dict[str, list[str]] = {}
    fixed_before: dict[str, str] = {}
    withdrawn = 0

    # Stream to a temp file rather than into memory — npm is ~204 MB and this
    # script runs on maintainer laptops as well as CI.
    with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # noqa: S310 (https only)
            shutil.copyfileobj(resp, tmp)
        tmp.flush()
        with zipfile.ZipFile(tmp.name) as zf:
            for entry in zf.namelist():
                if not entry.startswith("MAL-"):
                    continue
                try:
                    adv = json.loads(zf.read(entry))
                except (ValueError, KeyError):
                    continue
                # A retracted advisory must not keep flagging packages.
                if adv.get("withdrawn"):
                    withdrawn += 1
                    continue
                for affected in adv.get("affected", []):
                    purl = (affected.get("package") or {}).get("purl")
                    if not purl:
                        continue
                    ids.setdefault(purl, adv.get("id"))
                    vers = affected.get("versions") or []
                    if vers:
                        versions.setdefault(
                            purl, sorted(set(vers))[:MAX_VERSIONS_PER_PURL]
                        )
                        continue
                    # Case 3: a range that names the release the compromise
                    # ended at. Keep the boundary — without it every later
                    # version reads as malicious.
                    fixes = [
                        event["fixed"]
                        for rng in affected.get("ranges") or []
                        for event in rng.get("events") or []
                        if event.get("fixed")
                    ]
                    if fixes:
                        # Several ranges → the highest boundary is the one that
                        # keeps the flag conservative.
                        fixed_before.setdefault(purl, max(fixes, key=_version_key))
    if withdrawn:
        sys.stderr.write(
            f"[mal-snapshot] {name}: skipped {withdrawn} withdrawn advisory(ies)\n"
        )
    return ids, versions, fixed_before


def previous_purl_count() -> int | None:
    """PURL count of the vendored snapshot, or ``None`` if there isn't one."""
    if not OUT_PATH.exists():
        return None
    try:
        with OUT_PATH.open(encoding="utf-8") as fh:
            return len(json.load(fh).get("packages") or {})
    except (ValueError, OSError):
        return None


def main() -> int:
    packages: dict[str, str] = {}
    versions: dict[str, list[str]] = {}
    fixed_before: dict[str, str] = {}
    ok, failed, skipped = 0, [], []
    deadline = time.monotonic() + TOTAL_BUDGET

    for eco in ECOSYSTEMS:
        if time.monotonic() > deadline:
            skipped.append(eco)
            continue
        try:
            ids, vers, fixes = fetch_ecosystem(eco)
        except (urllib.error.URLError, OSError, ValueError, zipfile.BadZipFile) as exc:
            failed.append(eco)
            sys.stderr.write(f"[mal-snapshot] WARN: could not fetch {eco}: {exc}\n")
            continue
        packages.update(ids)
        versions.update(vers)
        fixed_before.update(fixes)
        ok += 1
        sys.stderr.write(f"[mal-snapshot] {eco}: {len(ids)} PURL(s)\n")

    if skipped:
        sys.stderr.write(
            f"[mal-snapshot] WARN: time budget ({TOTAL_BUDGET}s) spent; "
            f"skipped {len(skipped)}: {skipped}\n"
        )
    if ok == 0:
        sys.stderr.write(
            "[mal-snapshot] WARN: fetched 0 ecosystems; leaving the vendored "
            "snapshot untouched. Flagging keeps using the previous one.\n"
        )
        return 1

    # Hygiene floor — a partial fetch must not replace a good snapshot with a
    # fraction of one. Failing loudly beats silently un-flagging packages.
    previous = previous_purl_count()
    if previous and len(packages) < previous * HYGIENE_FLOOR:
        sys.stderr.write(
            f"[mal-snapshot] ABORT: {len(packages)} PURL(s) is below "
            f"{HYGIENE_FLOOR:.0%} of the previous {previous}. "
            f"Failed: {failed}; skipped: {skipped}. Not writing.\n"
        )
        return 1

    out = {
        "_snapshot": datetime.date.today().isoformat(),
        "_ecosystems": [e for e in ECOSYSTEMS if e not in failed and e not in skipped],
        "packages": packages,
        "versions": versions,
        "fixed_before": fixed_before,
    }
    MALICIOUS_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"), sort_keys=True)

    size = OUT_PATH.stat().st_size
    growth = (
        f", {len(packages) - previous:+d} vs previous {previous} "
        f"({(len(packages) / previous - 1) * 100:+.1f}%)"
        if previous
        else ""
    )
    sys.stderr.write(
        f"[mal-snapshot] wrote {len(packages)} PURL(s) "
        f"({len(versions)} version-pinned, {len(fixed_before)} range-bounded) "
        f"from {ok} ecosystem(s) to "
        f"{OUT_PATH.name}: {size:,} B, snapshot {out['_snapshot']}"
        f"{growth}; {len(failed)} failed: {failed}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
