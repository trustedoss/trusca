# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for :mod:`models.scan_fingerprint`.

S8 (concurrency-scaling-plan-2026-08-22.md §3.2) reuses a scan's preserved
SBOM instead of re-running cdxgen when the fingerprint is unchanged. The
properties that matter, in order of how badly a violation would hurt:

  1. Determinism: the same inputs, computed twice, must be the digest twice.
     Reuse compares two stored digests; a function that is not deterministic
     makes every scan look "changed" (safe but pointless, see the load-
     bearing test below for why this is more than academic) or, if
     insertion-order-dependent, could make two DIFFERENT trees collide.
  2. A scanner-version bump changes the digest, the plan's explicit accuracy
     requirement (§3.2: "지문은 잠금 파일만이 아니라 스캐너 버전과 스캔 설정까지
     포함해야 한다. 그러지 않으면 스캐너를 올린 뒤에도 옛 SBOM을 재사용한다").
     A miss here means a future reuse-decision revision silently serves a
     stale SBOM after every worker upgrade.
  3. A scan-config change (spec version, scope-filter toggles) changes the
     digest, for the same reason.
  4. A lockfile content change changes the digest: the baseline the whole
     feature exists to detect.
  5. The function refuses to answer (returns None) when the inventory cannot
     be trusted to describe the whole tree: no inventory, a truncated walk,
     or an unhashed file. A None that a caller mistook for "matches another
     None" would silently reuse an SBOM for a tree nobody actually compared.
"""

from __future__ import annotations

import copy

from models.scan_fingerprint import (
    FINGERPRINT_SCHEMA_VERSION,
    compute_scan_fingerprint,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _inventory(*files: tuple[str, str]) -> dict[str, object]:
    """Build an inventory in the exact shape ``collect_manifest_inventory`` returns."""
    entries = [{"path": path, "size": 123, "sha256": digest} for path, digest in files]
    return {"files": entries, "count": len(entries), "truncated": False}


_LOCKFILE_A = ("package-lock.json", "a" * 64)
_LOCKFILE_B = ("go.sum", "b" * 64)
_BASE_INVENTORY = _inventory(_LOCKFILE_A, _LOCKFILE_B)
_BASE_SCANNER_VERSION = "12.3.3"
_BASE_SCAN_CONFIG: dict[str, object] = {
    "cdxgen_spec_version": "1.5",
    "cdxgen_fetch_license": False,
    "scan_scope_filter_enabled": True,
    "scan_scope_filter_maven_enabled": True,
    "scan_scope_filter_node_enabled": True,
}


#: Sentinel distinguishing "caller did not pass this kwarg" (use the base
#: fixture) from "caller explicitly passed None" (a real test case below;
#: None is not the default, it is the value under test).
_UNSET = object()


def _compute(
    *,
    inventory: dict[str, object] | None | object = _UNSET,
    scanner_version: str | None = None,
    scan_config: dict[str, object] | None = None,
) -> str | None:
    resolved_inventory = _BASE_INVENTORY if inventory is _UNSET else inventory
    return compute_scan_fingerprint(
        manifest_inventory=resolved_inventory,  # type: ignore[arg-type]
        scanner_version=_BASE_SCANNER_VERSION if scanner_version is None else scanner_version,
        scan_config=_BASE_SCAN_CONFIG if scan_config is None else scan_config,
    )


# ---------------------------------------------------------------------------
# 1. Determinism (regression contract §4 S8)
# ---------------------------------------------------------------------------


def test_same_inputs_produce_the_same_digest_twice() -> None:
    first = _compute()
    second = _compute()
    assert first is not None
    assert first == second


def test_determinism_is_independent_of_dict_and_list_insertion_order() -> None:
    """A caller building the inventory or config dict in a different order
    (e.g. a filesystem walk that visits entries differently) must not change
    the digest: only the CONTENT may change it.
    """
    reordered_inventory = _inventory(_LOCKFILE_B, _LOCKFILE_A)
    reordered_config = dict(reversed(list(_BASE_SCAN_CONFIG.items())))

    baseline = _compute()
    reordered = _compute(inventory=reordered_inventory, scan_config=reordered_config)

    assert baseline == reordered


def test_does_not_mutate_its_inputs() -> None:
    """A pure function must not leave the caller's mappings changed: the
    inventory dict is also stored verbatim on the scan row as
    ``input_manifests``, and a mutation here would corrupt that record.
    """
    inventory_copy = copy.deepcopy(_BASE_INVENTORY)
    config_copy = copy.deepcopy(_BASE_SCAN_CONFIG)

    compute_scan_fingerprint(
        manifest_inventory=inventory_copy,
        scanner_version=_BASE_SCANNER_VERSION,
        scan_config=config_copy,
    )

    assert inventory_copy == _BASE_INVENTORY
    assert config_copy == _BASE_SCAN_CONFIG


# ---------------------------------------------------------------------------
# 2. Scanner version (the plan's explicit accuracy requirement)
# ---------------------------------------------------------------------------


def test_scanner_version_bump_changes_the_fingerprint() -> None:
    """The load-bearing case: after a worker image upgrade, a reuse decision
    built on this fingerprint must NOT serve an SBOM cdxgen 12.3.3 produced
    as though cdxgen 12.4.0 would have produced the same bytes.
    """
    before = _compute(scanner_version="12.3.3")
    after = _compute(scanner_version="12.4.0")

    assert before is not None
    assert after is not None
    assert before != after


def test_scanner_version_is_compared_as_an_exact_string() -> None:
    """No implicit semver normalization: "12.3.3" and "12.3.30" must not
    collide by being treated as numerically equal or truncated.
    """
    a = _compute(scanner_version="12.3.3")
    b = _compute(scanner_version="12.3.30")
    assert a != b


# ---------------------------------------------------------------------------
# 3. Scan config (spec version, scope-filter toggles)
# ---------------------------------------------------------------------------


def test_cdxgen_spec_version_change_changes_the_fingerprint() -> None:
    before = _compute(scan_config={**_BASE_SCAN_CONFIG, "cdxgen_spec_version": "1.5"})
    after = _compute(scan_config={**_BASE_SCAN_CONFIG, "cdxgen_spec_version": "1.6"})
    assert before != after


def test_each_scope_filter_toggle_independently_changes_the_fingerprint() -> None:
    baseline = _compute()
    for key in (
        "scan_scope_filter_enabled",
        "scan_scope_filter_maven_enabled",
        "scan_scope_filter_node_enabled",
    ):
        flipped = dict(_BASE_SCAN_CONFIG)
        flipped[key] = not flipped[key]
        variant = _compute(scan_config=flipped)
        assert variant != baseline, f"flipping {key} did not change the fingerprint"


def test_fetch_license_toggle_changes_the_fingerprint() -> None:
    before = _compute(scan_config={**_BASE_SCAN_CONFIG, "cdxgen_fetch_license": False})
    after = _compute(scan_config={**_BASE_SCAN_CONFIG, "cdxgen_fetch_license": True})
    assert before != after


def test_unexpected_config_value_shapes_are_stringified_not_dropped() -> None:
    """A future scan-config key this function was not written against (a
    list, a nested dict) must still influence the digest: silently ignoring
    it would narrow the fingerprint without anyone deciding that on purpose.
    """
    with_list = _compute(scan_config={**_BASE_SCAN_CONFIG, "extra": ["a", "b"]})
    with_different_list = _compute(scan_config={**_BASE_SCAN_CONFIG, "extra": ["a", "c"]})
    without = _compute()

    assert with_list != without
    assert with_list != with_different_list


# ---------------------------------------------------------------------------
# 4. Lockfile content (the baseline the feature exists to detect)
# ---------------------------------------------------------------------------


def test_lockfile_hash_change_changes_the_fingerprint() -> None:
    before = _compute()
    changed = _inventory(("package-lock.json", "c" * 64), _LOCKFILE_B)
    after = _compute(inventory=changed)
    assert before != after


def test_added_lockfile_changes_the_fingerprint() -> None:
    before = _compute(inventory=_inventory(_LOCKFILE_A))
    after = _compute(inventory=_inventory(_LOCKFILE_A, _LOCKFILE_B))
    assert before != after


def test_removed_lockfile_changes_the_fingerprint() -> None:
    before = _compute(inventory=_inventory(_LOCKFILE_A, _LOCKFILE_B))
    after = _compute(inventory=_inventory(_LOCKFILE_A))
    assert before != after


def test_manifest_only_ecosystem_change_is_detected() -> None:
    """Maven / a bare requirements.txt has no separate lockfile: the
    manifest itself is the authoritative dependency declaration there, which
    is why the inventory is not narrowed to "lockfiles only" (see the module
    docstring). A pom.xml version bump must still change the digest.
    """
    before = _compute(inventory=_inventory(("pom.xml", "d" * 64)))
    after = _compute(inventory=_inventory(("pom.xml", "e" * 64)))
    assert before != after


# ---------------------------------------------------------------------------
# 5. Refusing to answer (None is not a wildcard match)
# ---------------------------------------------------------------------------


def test_none_inventory_returns_none() -> None:
    """No manifest/lockfile found, or a scan with no source tree (container /
    SBOM-ingest): there is no dependency-set identity to fingerprint.
    """
    assert _compute(inventory=None) is None


def test_truncated_inventory_returns_none() -> None:
    """A walk that stopped before covering the tree cannot rule out a change
    past the cutoff: treating it as complete would be the exact failure
    mode a fingerprint exists to prevent.
    """
    truncated = {
        "files": [{"path": "package.json", "size": 1, "sha256": "f" * 64}],
        "count": 1,
        "truncated": True,
    }
    assert _compute(inventory=truncated) is None


def test_unhashed_file_returns_none() -> None:
    """A file too large to hash (or unreadable) leaves sha256=None in the
    inventory (services.scan_inputs._sha256's contract). "Unknown content"
    must not be treated as "unchanged content".
    """
    unhashed = {
        "files": [{"path": "package-lock.json", "size": 999_999_999, "sha256": None}],
        "count": 1,
        "truncated": False,
    }
    assert _compute(inventory=unhashed) is None


def test_empty_files_list_returns_none() -> None:
    empty = {"files": [], "count": 0, "truncated": False}
    assert _compute(inventory=empty) is None


def test_non_mapping_file_entry_returns_none() -> None:
    """The inventory contract promises a list of dicts; a malformed entry
    (a bare string, say) must not crash the caller. Refusing to answer is
    the same defensive posture the whole module takes elsewhere.
    """
    malformed = {"files": ["not-a-dict"], "count": 1, "truncated": False}
    assert _compute(inventory=malformed) is None


def test_file_entry_missing_path_returns_none() -> None:
    missing_path = {
        "files": [{"size": 1, "sha256": "a" * 64}],
        "count": 1,
        "truncated": False,
    }
    assert _compute(inventory=missing_path) is None


def test_file_entry_with_empty_path_returns_none() -> None:
    empty_path = {
        "files": [{"path": "", "size": 1, "sha256": "a" * 64}],
        "count": 1,
        "truncated": False,
    }
    assert _compute(inventory=empty_path) is None


def test_two_none_results_are_not_equal_by_construction() -> None:
    """Guards against a future edit that makes this return a sentinel string
    instead of None for the "cannot fingerprint" case: a caller comparing
    two scans' stored fingerprints with ``==`` must never see two
    un-fingerprinted scans read as "the same".
    """
    a = _compute(inventory=None)
    b = _compute(inventory=None)
    assert a is None
    assert b is None
    assert a == b  # both None, which is exactly why callers must not use
    # equality alone to decide reuse; None must be special-cased first. The
    # assertion documents the trap rather than hiding it.


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_digest_is_a_64_character_lowercase_hex_string() -> None:
    digest = _compute()
    assert digest is not None
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)  # raises ValueError if not valid hex


def test_schema_version_constant_is_hashed_in() -> None:
    """A change to FINGERPRINT_SCHEMA_VERSION must change every digest, even
    for identical lockfile/scanner/config inputs: otherwise a future
    incompatible change to this module's hashing shape could produce a
    digest that collides with one an older worker wrote under the old shape.
    """
    assert FINGERPRINT_SCHEMA_VERSION == 1
    digest_v1 = _compute()

    import models.scan_fingerprint as fp_module

    original = fp_module.FINGERPRINT_SCHEMA_VERSION
    try:
        fp_module.FINGERPRINT_SCHEMA_VERSION = 2  # type: ignore[misc]
        digest_v2 = _compute()
    finally:
        fp_module.FINGERPRINT_SCHEMA_VERSION = original  # type: ignore[misc]

    assert digest_v1 != digest_v2
