# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Read-time guidance join for the 2026 SBOM minimum elements.

Two things a reader needs that a verdict row does not carry: for an element the
SBOM does not satisfy, the CycloneDX fragment that would satisfy it; for an
element no scan can settle, what a person has to establish instead.

Joined at read time, like the regulatory crosswalk and for the same reasons —
the wording can be corrected without rescanning, and verdict rows stay the size
they were. It never changes a status, a counter, or the overall result.

Guidance is attached to a row that is NOT a pass, whatever the reason it is not
one. Upstream (sktelecom/bomlens#643) reached the same rule the hard way: it
first showed review notes only for elements with no automated source at all,
which hid the note on the signature element — the one row where a reader most
needs to be told that a detached signature is invisible to a check that reads a
single file.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_GUIDANCE_PATH = Path(__file__).with_name("cisa_guidance.json")


@lru_cache(maxsize=1)
def _guidance() -> dict[str, Any]:
    with _GUIDANCE_PATH.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):  # pragma: no cover — repo asset
        raise ValueError("cisa_guidance.json must be a JSON object")
    return loaded


def fragment_ids() -> frozenset[str]:
    """Element ids that carry a fill-in fragment."""
    return frozenset(_guidance().get("map") or {})


def review_ids() -> frozenset[str]:
    """Element ids that carry a human-review note."""
    return frozenset(_guidance().get("review") or {})


def attach_guidance(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return new check dicts with ``guidance`` / ``review`` joined by id.

    Input dicts are not mutated — the caller may be holding the raw JSONB row.
    A check that passes gets neither: a fragment telling someone how to supply
    a value they already supplied is noise.
    """
    data = _guidance()
    fragments = data.get("map") or {}
    reviews = data.get("review") or {}

    out: list[dict[str, Any]] = []
    for check in checks:
        joined = dict(check)
        if check.get("status") != "pass":
            check_id = check.get("id")
            fragment = fragments.get(check_id)
            review = reviews.get(check_id)
            if isinstance(fragment, dict):
                joined["guidance"] = dict(fragment)
            if isinstance(review, dict):
                joined["review"] = dict(review)
        out.append(joined)
    return out
