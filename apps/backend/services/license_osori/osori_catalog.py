# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Loader for the vendored OSORI licence snapshot (S5-B).

Mirrors ``services/eol/eol_catalog.py``: a JSON file beside the module, read
lazily, cached at module level because the file cannot change while the process
runs, and degrading to "no data" rather than raising when anything is off.
An operator override path exists for the same reason it does for EOL — so a
deployment can point at a newer snapshot without waiting for a release.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("license.osori")

_SNAPSHOT_PATH = Path(__file__).resolve().parent / "osori_snapshot.json"


def osori_snapshot_path() -> Path:
    """Where to read the snapshot from. Resolved at call time (rule #11).

    ``OSORI_SNAPSHOT_PATH`` lets an operator supply a fresher file than the one
    this release vendored. Empty or unset means the vendored copy.
    """
    override = (os.getenv("OSORI_SNAPSHOT_PATH") or "").strip()
    return Path(override) if override else _SNAPSHOT_PATH


def osori_enabled() -> bool:
    """Whether OSORI reference data is used at all. Resolved at call time.

    Default ON: the snapshot is a local file, so this adds no egress and no
    dependency — the flag exists for a deployment that would rather show only
    its own catalogue.
    """
    return (os.getenv("OSORI_ENABLED", "true") or "").strip().lower() not in {
        "false",
        "0",
        "no",
    }


@dataclass(frozen=True)
class OsoriLicense:
    """One licence as OSORI describes it.

    Deliberately NOT shaped like ``LicenseObligations`` from our own catalogue.
    The two answer overlapping questions with different vocabularies, and a
    shared shape would invite merging them — which would put an outside
    opinion behind a field the product presents as its own classification.
    """

    spdx_id: str
    name: str | None = None
    aliases: tuple[str, ...] = ()
    #: Whether the licence requires a notification when distributed.
    notification_required: bool | None = None
    #: How far source disclosure reaches: NONE / LIBRARY / EXECUTABLE / NETWORK.
    source_disclosure: str | None = None
    #: OSORI's caution items with a 1–5 level each.
    restrictions: tuple[tuple[str, int | None], ...] = field(default_factory=tuple)


def _parse_license(spdx_id: str, raw: dict[str, Any]) -> OsoriLicense:
    aliases = raw.get("nicknamelist")
    restrictions = raw.get("restrictions")
    return OsoriLicense(
        spdx_id=spdx_id,
        name=raw.get("name") if isinstance(raw.get("name"), str) else None,
        aliases=tuple(a for a in aliases if isinstance(a, str))
        if isinstance(aliases, list)
        else (),
        notification_required=raw.get("obligation_notification")
        if isinstance(raw.get("obligation_notification"), bool)
        else None,
        source_disclosure=raw.get("obligation_disclosing_src")
        if isinstance(raw.get("obligation_disclosing_src"), str)
        else None,
        restrictions=tuple(
            (item["name"], item.get("level") if isinstance(item.get("level"), int) else None)
            for item in restrictions
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        if isinstance(restrictions, list)
        else (),
    )


@lru_cache(maxsize=1)
def load_osori_snapshot() -> dict[str, OsoriLicense]:
    """``{spdx_id: OsoriLicense}``, or empty when the snapshot is unusable.

    Empty rather than an exception: this is supplementary reference data, and a
    malformed or missing file should cost the reader a panel, not the request.
    """
    if not osori_enabled():
        return {}
    path = osori_snapshot_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("osori_snapshot_unreadable", path=str(path), error=str(exc)[:200])
        return {}
    if not isinstance(payload, dict):
        return {}
    licenses = payload.get("licenses")
    if not isinstance(licenses, dict) or not licenses:
        log.warning("osori_snapshot_empty", path=str(path))
        return {}

    out: dict[str, OsoriLicense] = {}
    for spdx_id, raw in licenses.items():
        if isinstance(spdx_id, str) and isinstance(raw, dict):
            out[spdx_id] = _parse_license(spdx_id, raw)
    return out


def osori_source() -> str:
    """The attribution string ODC-By 1.0 requires wherever this data appears."""
    try:
        payload = json.loads(osori_snapshot_path().read_text(encoding="utf-8"))
        source = payload.get("_source")
        if isinstance(source, str) and source.strip():
            return source.strip()
    except (OSError, ValueError):
        pass
    return "OSORI (olis.or.kr), ODC-By 1.0"


def osori_license(spdx_id: str) -> OsoriLicense | None:
    """OSORI's record for *spdx_id*, or ``None``."""
    if not spdx_id:
        return None
    return load_osori_snapshot().get(spdx_id)


@lru_cache(maxsize=1)
def osori_alias_map() -> dict[str, str]:
    """``{lowercased alias: spdx_id}`` for every alias in the snapshot.

    Lowercased because the spellings this is meant to catch differ in case as
    often as in punctuation. An alias claimed by two licences is dropped rather
    than resolved arbitrarily — a wrong SPDX id is worse than no answer, since
    it silently reclassifies a component's obligations.
    """
    claims: dict[str, set[str]] = {}
    for record in load_osori_snapshot().values():
        for alias in record.aliases:
            key = alias.strip().lower()
            if key:
                claims.setdefault(key, set()).add(record.spdx_id)

    resolved: dict[str, str] = {}
    for key, owners in claims.items():
        if len(owners) == 1:
            resolved[key] = next(iter(owners))
        else:
            log.info("osori_alias_ambiguous", alias=key, owners=sorted(owners))
    return resolved


__all__ = [
    "OsoriLicense",
    "load_osori_snapshot",
    "osori_alias_map",
    "osori_enabled",
    "osori_license",
    "osori_snapshot_path",
    "osori_source",
]
