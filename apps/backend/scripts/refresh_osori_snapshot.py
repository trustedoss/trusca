#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Refresh the vendored OSORI licence snapshot.

    python3 scripts/refresh_osori_snapshot.py

Run by a maintainer, not by the product — same arrangement as
``refresh_eol_snapshot.py``. OSORI moves slowly (96 records edited in the first
half of 2026), so a refresh per release is ample and a scheduled task would be
noise.

What OSORI is
-------------
An open licence database built jointly by Korean companies (Samsung, LG, Kakao,
SK telecom, Hyundai, CJ among them) and hosted by the Korea Copyright
Commission since November 2025. Public, unauthenticated, 5,000 requests/hour.
Its data is ODC-By 1.0 — attribution only, so it can be vendored and
redistributed inside an Apache-2.0 product as long as the source is credited.

What we keep, and what we do not
--------------------------------
Only the licence table (669 rows), and only a handful of fields from it. The OSS
component table (65k rows) is deliberately skipped: ClearlyDefined covers the
same ground at 55M definitions and its version-level rows are fresher.

``description_ko`` is NOT kept even though the field exists. Sampling the live
API found it empty on every record checked, and this product already ships its
own Korean summaries for all 52 catalogue licences — there is nothing to gain
and a null column to explain.

The field whitelist matters for size as much as for focus: the raw response
carries full licence texts, and this repository already vendors those
separately under ``services/license_texts/``.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "services"
    / "license_osori"
    / "osori_snapshot.json"
)

BASE = "https://www.olis.or.kr:15443/api/v2/user/licenses"
PAGE_SIZE = 200
REQUEST_TIMEOUT = 20
#: OSORI allows 5,000 requests/hour. The listing is four pages; the SPDX id and
#: the aliases only exist on the per-licence detail route, so the run costs one
#: request per licence on top of that — ~673 against a 5,000/hour budget.
MAX_PAGES = 40
#: Politeness gap between detail calls. Nothing requires it; the budget is
#: generous. It keeps a maintainer's refresh from looking like a scrape.
DETAIL_INTERVAL_SECONDS = 0.05

#: Fields kept per licence. Everything else — including the full licence text,
#: which this repository already vendors under services/license_texts/ — is
#: dropped.
KEEP = ("name", "spdx_identifier", "nicknamelist")
#: Obligation metadata worth carrying: what a licence demands, in OSORI's own
#: vocabulary. Used as reference material next to our own catalogue, never
#: merged into it.
KEEP_OBLIGATIONS = ("obligation_notification", "obligation_disclosing_src")


def _rows(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    """OSORI wraps every answer in ``messageList``; listings and details differ.

    Listings come back under ``messageList.list``, details under
    ``messageList.detailInfo``. Neither shape is documented, so both are read
    defensively — a change in envelope should yield zero rows and abort the
    refresh, not a half-written snapshot.
    """
    if not isinstance(payload, dict):
        return []
    envelope = payload.get("messageList")
    if not isinstance(envelope, dict):
        return []
    rows = envelope.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _parse_nicknames(raw: Any) -> list[str]:
    """``nicknamelist`` arrives as a JSON-encoded string, not an array."""
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            candidates = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return []
    else:
        return []
    if not isinstance(candidates, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            out.append(cleaned)
    return out


def _get(url: str) -> dict[str, Any] | None:
    request = urllib.request.Request(  # noqa: S310 - fixed https host, no user input
        url, headers={"Accept": "application/json", "User-Agent": "TrustedOSS-Portal/0.1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"WARN: {url} failed: {exc}", file=sys.stderr)
        return None


def _slim(record: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce one OSORI record to the fields we vendor.

    Records without an SPDX identifier are dropped: the whole point of the
    snapshot is to key on SPDX, and a record we cannot key is unusable.
    """
    spdx = record.get("spdx_identifier")
    if not isinstance(spdx, str) or not spdx.strip():
        return None

    out: dict[str, Any] = {"spdx_identifier": spdx.strip()}
    name = record.get("name")
    if isinstance(name, str) and name.strip():
        out["name"] = name.strip()
    nicknames = _parse_nicknames(record.get("nicknamelist"))
    if nicknames:
        out["nicknamelist"] = nicknames
    for field in KEEP_OBLIGATIONS:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = value.strip()
        elif isinstance(value, bool):
            out[field] = value

    restrictions = record.get("restrictionlist")
    if isinstance(restrictions, list):
        kept = [
            {"name": item.get("name"), "level": item.get("level")}
            for item in restrictions
            if isinstance(item, dict) and item.get("name")
        ]
        if kept:
            out["restrictions"] = kept
    return out


def main() -> int:
    # Pass 1 — the listing, for the licence names. It carries no SPDX id and no
    # aliases, which is why pass 2 exists.
    names: list[str] = []
    for page in range(MAX_PAGES):
        rows = _rows(_get(f"{BASE}?page={page}&size={PAGE_SIZE}"), "list")
        if not rows:
            break
        for record in rows:
            name = record.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        if len(rows) < PAGE_SIZE:
            break

    # Pass 2 — one detail call per licence for the SPDX id and the aliases.
    licences: dict[str, dict[str, Any]] = {}
    aliases_seen = 0
    for index, name in enumerate(names):
        if index:
            time.sleep(DETAIL_INTERVAL_SECONDS)
        detail_rows = _rows(
            _get(f"{BASE}/name?searchWord={quote(name, safe='')}"), "detailInfo"
        )
        for record in detail_rows:
            slim = _slim(record)
            if slim is None:
                continue
            licences[slim["spdx_identifier"]] = slim
            aliases_seen += len(slim.get("nicknamelist") or [])

    # A network failure must not destroy the vendored file. Same rule as the
    # EOL refresher: write only when the fetch actually produced something.
    if not licences:
        print("ERROR: no licences fetched — leaving the snapshot untouched", file=sys.stderr)
        return 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {"_source": "OSORI (olis.or.kr), ODC-By 1.0", "licenses": licences},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {len(licences)} licences ({aliases_seen} aliases) "
        f"→ {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
