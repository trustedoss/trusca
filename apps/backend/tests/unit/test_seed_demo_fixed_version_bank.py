"""
``_FIXED_VERSION_BANK`` (scripts/seed_demo.py) must keep CVE-2024-99001
(lodash) unfixed, #401.

The vendored verify-specs oracle's F_NEW fixture
(tests/verify-specs/specs/vulnerabilities.json) looks up the finding for
this exact CVE id and asserts fixed_version is null ("fix version unknown"),
across three checks (TC-VULN-06-005/009/011). PR #444 gave every seeded CVE
a fixed version, including this one, and broke all three nightly checks
without a local test catching it. This pins the bank directly, no DB
needed, so the next edit to the bank can't silently reintroduce the same
regression.
"""

from __future__ import annotations


def test_lodash_cve_stays_unfixed_for_the_verify_specs_oracle() -> None:
    from scripts.seed_demo import _CVE_PLAN, _FIXED_VERSION_BANK

    idx = _CVE_PLAN.index("CVE-2024-99001")
    assert _FIXED_VERSION_BANK[idx] is None


def test_at_least_one_entry_stays_null_for_the_no_known_fix_empty_state() -> None:
    from scripts.seed_demo import _FIXED_VERSION_BANK

    assert None in _FIXED_VERSION_BANK
