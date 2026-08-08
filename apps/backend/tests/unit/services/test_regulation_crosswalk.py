"""
Unit + contract tests for ``services.regulation_crosswalk``.

Contract focus (CLAUDE.md §2 rule 2 — same vocabulary in two places needs a
reconciliation test): the vendored ``regulation_crosswalk.json`` keys must
resolve to check ids this codebase actually emits (core conformance ids or G7
element ids), and every mapped framework must be declared in the file's own
``frameworks`` table. An upstream refresh that renames a check id fails here
loudly instead of silently dropping regulatory references.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import regulation_crosswalk as rc
from services import sbom_conformance as sc

_SERVICES = Path(rc.__file__).parent
_G7_REGISTRY = _SERVICES / "g7_registry.json"
_CISA_REGISTRY = _SERVICES / "cisa_registry.json"


def _vendored() -> dict:
    with (_SERVICES / "regulation_crosswalk.json").open(encoding="utf-8") as fh:
        loaded: dict = json.load(fh)
        return loaded


def _registry_element_ids(path: Path) -> set[str]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    return {
        element["id"]
        for cluster in registry.get("clusters", [])
        for element in cluster.get("elements", [])
    }


def _g7_element_ids() -> set[str]:
    return _registry_element_ids(_G7_REGISTRY)


# ---------------------------------------------------------------------------
# Vendored-catalogue contracts.
# ---------------------------------------------------------------------------
def test_every_map_key_resolves_to_a_known_check_id() -> None:
    known = (
        set(sc.CHECK_IDS)
        | _g7_element_ids()
        | _registry_element_ids(_CISA_REGISTRY)
        | rc.UNPORTED_CHECK_IDS
    )
    unknown = set(_vendored()["map"]) - known
    assert not unknown, (
        f"crosswalk keys with no matching check/element: {sorted(unknown)} — "
        "either port the upstream check or add it to UNPORTED_CHECK_IDS with "
        "a rationale"
    )


def test_unported_ids_are_actually_unported() -> None:
    """The allowlist must shrink the moment one of its checks gets ported —
    a stale allowlist would silently hide a future drift."""
    assert not (rc.UNPORTED_CHECK_IDS & set(sc.CHECK_IDS))


def test_every_mapped_framework_is_declared() -> None:
    vendored = _vendored()
    declared = set(vendored["frameworks"])
    used = {
        entry["framework"]
        for entries in vendored["map"].values()
        for entry in entries
    }
    assert used <= declared


def test_regulatory_field_checks_all_have_crosswalk_entries() -> None:
    """The five regulatory field checks exist BECAUSE the crosswalk names
    them — each must map to at least one framework."""
    mapping = _vendored()["map"]
    for check_id in sc.REGULATORY_FIELD_CHECK_IDS:
        assert mapping.get(check_id), check_id


def test_disclaimers_present_in_both_languages() -> None:
    vendored = _vendored()
    assert vendored["disclaimer"].strip()
    assert vendored["disclaimer_ko"].strip()


#: Every claim the disclaimer has to make, as (English, Korean) wording. Both
#: strings are shown to users — one to each audience — so a sentence added to
#: one and not the other silently leaves that audience with a weaker notice.
#: Presence-only assertions cannot see that; this pairs the two.
_DISCLAIMER_CLAIMS = [
    ("EU AI Act", "EU 인공지능법"),
    ("Korean AI Framework Act", "AI 기본법"),
    ("EU Cyber Resilience Act", "EU 사이버복원력법"),
    ("any other regulation", "어떤 규제"),
    ("bias and fairness assessment", "공정성·편향 평가"),
    ("risk management", "위험관리"),
    ("human oversight", "인간 감독"),
    ("separate documents", "별도 문서"),
]


@pytest.mark.parametrize(("english", "korean"), _DISCLAIMER_CLAIMS)
def test_both_disclaimers_make_the_same_claims(english: str, korean: str) -> None:
    vendored = _vendored()
    assert english in vendored["disclaimer"], f"English disclaimer dropped: {english}"
    assert korean in vendored["disclaimer_ko"], f"Korean disclaimer dropped: {korean}"


# ---------------------------------------------------------------------------
# Join behaviour (attach_regulations).
# ---------------------------------------------------------------------------
def test_attach_joins_refs_with_framework_short_names() -> None:
    checks = [
        {"id": "hash-algorithm", "status": "warn", "detail": "0% (0/3)"},
        {"id": "timestamp", "status": "pass", "detail": "2026-01-01"},
        {"id": "not-a-real-check", "status": "pass", "detail": ""},
    ]
    joined = rc.attach_regulations(checks)
    by_id = {c["id"]: c for c in joined}
    bsi = by_id["hash-algorithm"]["regulations"]
    assert bsi and bsi[0]["framework"] == "bsi-tr-03183-2"
    assert bsi[0]["short"] == "BSI TR-03183-2"
    assert bsi[0]["short_ko"]
    assert bsi[0]["ref"] and bsi[0]["basis"]
    # `timestamp` is TRUSCA's own format check and maps to BSI only. The US
    # framework moved to the 2026 elements, whose ids are `cisa-*` — the 2021
    # mappings sat on the format checks, so keeping them there would count one
    # 2026 requirement once per format row that used to carry it.
    assert {r["framework"] for r in by_id["timestamp"]["regulations"]} == {
        "bsi-tr-03183-2"
    }
    # Unmapped id: empty list, never a KeyError.
    assert by_id["not-a-real-check"]["regulations"] == []


def test_attach_does_not_mutate_input() -> None:
    checks = [{"id": "timestamp", "status": "pass", "detail": ""}]
    rc.attach_regulations(checks)
    assert "regulations" not in checks[0]


# ---------------------------------------------------------------------------
# Rollup behaviour (crosswalk_summary) — BomLens XW_SUMMARY parity.
# ---------------------------------------------------------------------------
def test_rollup_counts_mirror_upstream_formula() -> None:
    """present = pass; gap = warn with an automated source; review = warn with
    source 'na'; failed = fail. The crosswalk is still not a second verdict —
    it reports the failure, it does not re-decide it — but the number is stated
    rather than left as the remainder a consumer has to compute."""
    joined = rc.attach_regulations(
        [
            {"id": "timestamp", "status": "fail", "detail": ""},
            {"id": "license", "status": "warn", "detail": "40% (2/5)"},
            {"id": "hash", "status": "pass", "detail": "100% (5/5)"},
            {"id": "file-properties", "status": "warn", "source": "na", "detail": ""},
        ]
    )
    summary = rc.crosswalk_summary(joined)
    frameworks = {f["id"]: f for f in summary["frameworks"]}
    bsi = frameworks["bsi-tr-03183-2"]
    assert bsi["total"] == 4
    assert bsi["present"] == 1  # hash
    assert bsi["gap"] == 1  # license (warn, automated)
    assert bsi["review"] == 1  # file-properties (warn, source na)
    assert bsi["failed"] == 1  # timestamp
    element_ids = {e["id"] for e in bsi["elements"]}
    assert element_ids == {"timestamp", "license", "hash", "file-properties"}
    refs = next(e for e in bsi["elements"] if e["id"] == "hash")["refs"]
    assert refs == ["Section 5.2.2"]


def test_the_four_counters_partition_every_framework_total() -> None:
    """Whatever the mix of statuses, present + gap + review + failed == total.

    This is the property that was missing. Without it a status that fits none
    of the named counters sits in the gap between them, and the only way to see
    it is to subtract — which is how the most serious category came to be
    displayed under the mildest name available.
    """
    joined = rc.attach_regulations(
        [
            {"id": "timestamp", "status": "fail", "detail": ""},
            {"id": "license", "status": "warn", "detail": ""},
            {"id": "hash", "status": "pass", "detail": ""},
            {"id": "file-properties", "status": "warn", "source": "na", "detail": ""},
            {"id": "component-creator", "status": "pass", "source": "na", "detail": ""},
            {"id": "cisa-sbom-timestamp", "status": "pass", "detail": ""},
            {"id": "cisa-coverage", "status": "warn", "source": "na", "detail": ""},
        ]
    )
    frameworks = rc.crosswalk_summary(joined)["frameworks"]
    assert frameworks
    for framework in frameworks:
        counted = (
            framework["present"]
            + framework["gap"]
            + framework["review"]
            + framework["failed"]
        )
        assert counted == framework["total"], framework["id"]


def test_rollup_omits_frameworks_with_no_mapped_checks() -> None:
    """A plain dependency SBOM maps to BSI + NTIA only — the AI frameworks
    (joined via g7-* element ids) must not appear as empty rows."""
    joined = rc.attach_regulations([{"id": "timestamp", "status": "pass", "detail": ""}])
    summary = rc.crosswalk_summary(joined)
    ids = [f["id"] for f in summary["frameworks"]]
    assert "eu-ai-act" not in ids
    assert "kr-ai-framework-act" not in ids
    assert set(ids) <= {"bsi-tr-03183-2", "us-sbom-minimum-elements"}


def test_rollup_gains_ai_frameworks_for_g7_elements() -> None:
    joined = rc.attach_regulations(
        [
            {"id": "timestamp", "status": "pass", "detail": ""},
            {"id": "g7-model-training", "status": "warn", "source": "inferred", "detail": ""},
        ]
    )
    ids = [f["id"] for f in rc.crosswalk_summary(joined)["frameworks"]]
    assert "eu-ai-act" in ids
    assert "kr-ai-framework-act" in ids


def test_rollup_framework_order_follows_vendored_file() -> None:
    joined = rc.attach_regulations(
        [
            {"id": "timestamp", "status": "pass", "detail": ""},
            {"id": "g7-model-training", "status": "pass", "detail": ""},
        ]
    )
    got = [f["id"] for f in rc.crosswalk_summary(joined)["frameworks"]]
    vendored_order = [fid for fid in _vendored()["frameworks"] if fid in got]
    assert got == vendored_order


def test_summary_carries_bilingual_metadata() -> None:
    joined = rc.attach_regulations([{"id": "timestamp", "status": "pass", "detail": ""}])
    summary = rc.crosswalk_summary(joined)
    assert summary["disclaimer"] and summary["disclaimer_ko"]
    for framework in summary["frameworks"]:
        assert framework["title"] and framework["title_ko"]
        assert framework["short"] and framework["short_ko"]
