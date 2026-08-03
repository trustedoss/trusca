# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
OSORI licence reference data (S5-B).

Two things this repository does not otherwise have:

**Aliases.** 222 of the 671 licences carry the non-standard spellings people
actually write — ``"The MIT License (MIT)"``, ``"Apache 2"``,
``"Android-Apache-2.0"``. `services.license_normalize` handles the shapes it
was written for and returns ``None`` for anything else rather than guessing;
these fill in a chunk of that gap with curated answers instead of new
heuristics.

**Obligation metadata beyond our 52.** The built-in catalogue classifies 52
licences; everything else lands as ``category="unknown"`` with a null summary.
OSORI describes 671 — what each demands in notification terms, how far source
disclosure reaches (``NONE`` / ``LIBRARY`` / ``EXECUTABLE`` / ``NETWORK``), and
a 1–5 caution level. That is shown as *reference material* beside our own
classification, never merged into it: the two vocabularies are different, and
ours is the one pinned by contract tests.

Provenance and licence: OSORI is an open licence database built jointly by
Korean companies and hosted by the Korea Copyright Commission. Its data is
ODC-By 1.0 — attribution only — so it is vendored here with the source credited
in the snapshot itself and in the product's licence notices.

The snapshot is a file, not a call. Refreshed by a maintainer running
``scripts/refresh_osori_snapshot.py``; the product never reaches olis.or.kr at
runtime, so air-gapped installs get the same answers as connected ones.
"""

from __future__ import annotations

from services.license_osori.osori_catalog import (
    OsoriLicense,
    load_osori_snapshot,
    osori_alias_map,
    osori_license,
    osori_source,
)

__all__ = [
    "OsoriLicense",
    "load_osori_snapshot",
    "osori_alias_map",
    "osori_license",
    "osori_source",
]
