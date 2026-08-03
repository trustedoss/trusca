# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
OSORI reference catalogue — unit tests (S5-B).

Run against the vendored snapshot rather than a fixture: the snapshot IS the
artefact under test, and a synthetic stand-in would pass while the real file
was empty, mis-keyed, or missing the aliases that are the whole point.
"""

from __future__ import annotations

import json

import pytest

from services.license_normalize import normalize_license_name
from services.license_osori import (
    load_osori_snapshot,
    osori_alias_map,
    osori_license,
    osori_source,
)
from services.license_osori.osori_catalog import osori_snapshot_path


def test_the_vendored_snapshot_is_present_and_populated() -> None:
    snapshot = load_osori_snapshot()
    # 669 was the count OSORI reported when this was captured; the assertion is
    # a floor, not an equality, so a refresh that adds licences does not fail.
    assert len(snapshot) >= 600


def test_every_record_is_keyed_by_its_own_spdx_id() -> None:
    for spdx_id, record in load_osori_snapshot().items():
        assert record.spdx_id == spdx_id


def test_the_snapshot_carries_its_attribution() -> None:
    """ODC-By 1.0 requires the source to be credited wherever the data appears."""
    payload = json.loads(osori_snapshot_path().read_text(encoding="utf-8"))
    assert "OSORI" in payload["_source"]
    assert "ODC-By" in payload["_source"]
    assert "OSORI" in osori_source()


def test_obligation_metadata_reaches_licenses_outside_our_catalog() -> None:
    """The reason to carry this at all.

    Our own catalogue classifies 52 licences and says nothing about the rest.
    OSORI describes how far source disclosure reaches for hundreds more.
    """
    from tasks.scan_source import _LICENSE_CATEGORY_DEFAULTS

    snapshot = load_osori_snapshot()
    outside = set(snapshot) - set(_LICENSE_CATEGORY_DEFAULTS)
    assert len(outside) >= 400, "OSORI should reach well past our own 52"

    described = [
        spdx_id
        for spdx_id in outside
        if snapshot[spdx_id].source_disclosure is not None
    ]
    assert described, "and it should actually describe some of them"


def test_gpl_reports_a_wider_disclosure_reach_than_mit() -> None:
    """A sanity check on the field's meaning, not just its presence."""
    gpl = osori_license("GPL-3.0-only")
    mit = osori_license("MIT")
    assert gpl is not None and mit is not None
    assert gpl.source_disclosure in {"EXECUTABLE", "NETWORK", "LIBRARY"}
    assert mit.source_disclosure == "NONE"


def test_unknown_spdx_id_yields_nothing(  ) -> None:
    assert osori_license("NoSuchLicense-9.9") is None
    assert osori_license("") is None


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------


def test_the_alias_map_is_populated() -> None:
    assert len(osori_alias_map()) >= 400


def test_an_alias_never_maps_to_two_licenses() -> None:
    """Ambiguity is dropped upstream; nothing here should resolve arbitrarily."""
    for alias, spdx_id in osori_alias_map().items():
        assert isinstance(spdx_id, str) and spdx_id
        assert alias == alias.lower()


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        # Spellings the hand-written rules were not built for.
        ("The MIT License (MIT)", "MIT"),
        ("Apache 2", "Apache-2.0"),
        ("Apache License v2", "Apache-2.0"),
    ],
)
def test_osori_aliases_reach_normalize_license_name(
    written: str, expected: str
) -> None:
    assert normalize_license_name(written) == expected


def test_the_rules_still_win_over_the_alias_list() -> None:
    """Order matters: `_RULES` is hand-tuned and OSORI is the fall-through.

    A compound expression must still come back as None — the rules catch it
    before the alias lookup ever runs, and an alias table cannot be allowed to
    turn "MIT OR Apache-2.0" into a single id.
    """
    assert normalize_license_name("MIT OR Apache-2.0") is None
    assert normalize_license_name("GPL-2.0 and MIT") is None


def test_unknown_names_still_return_none() -> None:
    """The fall-through adds answers; it must not start guessing."""
    assert normalize_license_name("total nonsense here") is None
    assert normalize_license_name("") is None
    assert normalize_license_name(None) is None
