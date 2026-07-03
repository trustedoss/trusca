"""
Unit tests for ``services.g7_conformance`` — G7 AI SBOM minimum elements.

Pure-function tests (no DB / redis — local lane). The oracle document is the
REAL OWASP AIBOM Generator 1.7 ML-BOM recorded under
``tests/fixtures/sbom_ingest/aibom-owasp-1_7.json`` (CLAUDE.md §2 rule 3 — no
hand-built minimal SBOMs for the boundary cases); crafted inputs cover the
adversarial-shape and clamp edges only.

Contract tests (§2 rule 2): the vendored registry (v2, sbom-tools#306) is the
single source of truth for element metadata, and the hand-ported predicate /
missingPath / evidence maps must cover EXACTLY their registry subsets — a
registry refresh with a missed port fails here immediately.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services import g7_conformance as g7
from services.sbom_conformance import Check

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "sbom_ingest"
    / "aibom-owasp-1_7.json"
)

# ---------------------------------------------------------------------------
# Explicit per-element expectations against the real fixture. Computed from the
# fixture's actual values (e.g. the bert ML component HAS purl / licenses /
# modelCard.modelParameters but NO hashes / top-level properties; the document
# has NO metadata.authors / signature / data components / vulnerabilities).
# Registry v2 (#306): the 13 models-cluster elements score per-model coverage
# ("N/M model component(s)" + offender names); the fixture has exactly ONE
# model, "bert-base-uncased".
# ---------------------------------------------------------------------------
FIXTURE_MODEL = "bert-base-uncased"

EXPECTED_PASS = frozenset(
    {
        "g7-meta-version",
        "g7-meta-format-name",
        "g7-meta-format-version",
        "g7-meta-tool-name",
        "g7-meta-tool-version",
        "g7-meta-timestamp",
        "g7-meta-dependency",
        "g7-slp-name",
        "g7-slp-version",
        "g7-slp-timestamp",
        # modelCard.modelParameters.datasets — found by the `..` descent port.
        "g7-ds-name",
    }
)
EXPECTED_ABSENT = frozenset(
    {
        "g7-meta-author",
        "g7-meta-signature",
        "g7-meta-gen-context",
        "g7-slp-components",  # exactly 1 component — needs > 1
        "g7-slp-producer",  # no metadata.component.publisher / metadata.supplier
        "g7-model-openness",  # no openness:* props, no openness prose anywhere
        "g7-ds-description",  # no type=="data" components
        "g7-ds-identifier",
        "g7-ds-provenance",
        "g7-ds-license",
        "g7-infra-software",  # only the ML component — no library/app/framework
        "g7-infra-hardware",
        "g7-sec-vulns",
        "g7-kpi-operational",  # no performanceMetrics
    }
)
# missingPath elements the single bert model satisfies → pass "1/1".
EXPECTED_MODEL_PASS = frozenset(
    {
        "g7-model-name",
        "g7-model-id",
        "g7-model-version",
        "g7-model-producer",
        "g7-model-description",
        "g7-model-card",
        "g7-model-training",
        "g7-model-license",
        "g7-model-extref",
    }
)
# missingPath elements the bert model lacks → warn "0/1" + offender name.
EXPECTED_MODEL_MISSING = frozenset(
    {
        "g7-model-timestamp",  # no component-level properties
        "g7-model-hash-value",
        "g7-model-hash-alg",
        "g7-model-io",  # modelParameters has no inputs/outputs
    }
)
EXPECTED_REVIEW = frozenset(
    {
        "g7-slp-data-flow",
        "g7-slp-data-usage",
        "g7-slp-io",
        "g7-slp-app-area",
        "g7-ds-content",
        "g7-ds-hash",
        "g7-ds-statistics",
        "g7-ds-sensitivity",
        "g7-ds-dependency",
        "g7-sec-controls",
        "g7-sec-compliance",
        "g7-sec-policy",
        "g7-kpi-security",
    }
)
ALL_IDS = (
    EXPECTED_PASS
    | EXPECTED_ABSENT
    | EXPECTED_MODEL_PASS
    | EXPECTED_MODEL_MISSING
    | EXPECTED_REVIEW
)

# id → (status, detail, missing) — offender lists are non-empty ONLY on the
# per-model-coverage warns.
_STATUS_BY_ID: dict[str, tuple[str, str, list[str]]] = (
    {i: ("pass", "present", []) for i in EXPECTED_PASS}
    | {i: ("warn", "not present in the SBOM", []) for i in EXPECTED_ABSENT}
    | {i: ("pass", "1/1 model component(s)", []) for i in EXPECTED_MODEL_PASS}
    | {
        i: ("warn", "0/1 model component(s)", [FIXTURE_MODEL])
        for i in EXPECTED_MODEL_MISSING
    }
    | {
        i: ("warn", "requires human review (no automated source)", [])
        for i in EXPECTED_REVIEW
    }
)


def _fixture_doc() -> dict[str, Any]:
    loaded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def fixture_checks() -> dict[str, Check]:
    return {c.id: c for c in g7.evaluate_g7(_fixture_doc())}


@pytest.fixture(scope="module")
def registry_by_id() -> dict[str, tuple[str, dict[str, Any]]]:
    return {str(e.get("id")): (cid, e) for cid, e in g7.iter_elements()}


# ---------------------------------------------------------------------------
# Expectation-set hygiene + full-registry coverage.
# ---------------------------------------------------------------------------
def test_expectation_sets_are_disjoint_and_cover_all_51_elements() -> None:
    assert (
        len(EXPECTED_PASS)
        + len(EXPECTED_ABSENT)
        + len(EXPECTED_MODEL_PASS)
        + len(EXPECTED_MODEL_MISSING)
        + len(EXPECTED_REVIEW)
        == 51
    )
    assert len(ALL_IDS) == 51, "the expectation sets must be disjoint"
    registry_ids = {str(e.get("id")) for _, e in g7.iter_elements()}
    assert registry_ids == ALL_IDS


def test_evaluate_emits_all_elements_in_registry_order(
    fixture_checks: dict[str, Check],
) -> None:
    ordered = [c.id for c in g7.evaluate_g7(_fixture_doc())]
    assert ordered == [str(e.get("id")) for _, e in g7.iter_elements()]
    assert len(fixture_checks) == 51


# ---------------------------------------------------------------------------
# 51-element exhaustive parametrisation against the real fixture.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("element_id", sorted(ALL_IDS))
def test_fixture_element_verdict(
    element_id: str,
    fixture_checks: dict[str, Check],
    registry_by_id: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    check = fixture_checks[element_id]
    status, detail, missing = _STATUS_BY_ID[element_id]
    assert check.status == status
    assert check.detail == detail
    assert check.missing == missing
    # Advisory contract: G7 checks never gate the verdict.
    assert check.required is False
    # Metadata is carried verbatim from the registry (single source of truth).
    cluster_id, element = registry_by_id[element_id]
    assert check.cluster == cluster_id
    assert check.source == element.get("source")
    assert check.role == element.get("role")
    assert check.label == element.get("label")


# ---------------------------------------------------------------------------
# Registry ↔ port contract (CLAUDE.md §2 rule 2 — same-vocabulary-two-places).
# ---------------------------------------------------------------------------
def test_predicates_cover_exactly_the_cdxpath_elements() -> None:
    assert g7.automated_element_ids() == frozenset(g7._PREDICATES)
    assert len(g7._PREDICATES) == 25  # registry v2 (#306)


def test_missing_ports_cover_exactly_the_missingpath_elements() -> None:
    assert g7.missing_element_ids() == frozenset(g7._MISSING)
    assert len(g7._MISSING) == 13  # registry v2 (#306) — models cluster


def test_evidence_extractors_cover_exactly_the_evidence_elements() -> None:
    assert g7.evidence_element_ids() == frozenset(g7._EVIDENCE)


def test_element_shapes_partition_the_registry() -> None:
    """cdxPath / missingPath / na are pairwise disjoint and cover all 51."""
    cdx = g7.automated_element_ids()
    missing = g7.missing_element_ids()
    na_ids = {
        str(e.get("id")) for _, e in g7.iter_elements() if e.get("source") == "na"
    }
    assert not cdx & missing
    assert not cdx & na_ids
    assert not missing & na_ids
    assert len(cdx | missing | na_ids) == 51


def test_g7_seed_plan_is_a_verbatim_evaluator_capture(
    fixture_checks: dict[str, Check],
) -> None:
    """Hardening rule 2 (same vocabulary, two places): the ``--with-g7`` seed
    plan in scripts/seed_e2e_user.py pins status/detail/evidence/missing
    strings that claim to be captured from THIS evaluator over the same
    fixture — assert they never drift (registry v2 changed the models-cluster
    details to "N/M model component(s)")."""
    from scripts.seed_e2e_user import _G7_SEED_PLAN

    for element_id, (status, detail, evidence, missing) in _G7_SEED_PLAN.items():
        check = fixture_checks[element_id]
        assert (status, detail, evidence, missing) == (
            check.status,
            check.detail,
            check.evidence,
            check.missing,
        ), element_id


def test_na_elements_have_no_port() -> None:
    na_ids = {
        str(e.get("id")) for _, e in g7.iter_elements() if e.get("source") == "na"
    }
    assert na_ids == EXPECTED_REVIEW
    assert not na_ids & set(g7._PREDICATES)
    assert not na_ids & set(g7._MISSING)


# ---------------------------------------------------------------------------
# Evidence extraction on the real fixture.
# ---------------------------------------------------------------------------
def test_model_id_evidence_is_the_fixture_purl(
    fixture_checks: dict[str, Check],
) -> None:
    assert fixture_checks["g7-model-id"].evidence == [
        "pkg:huggingface/google-bert/bert-base-uncased@86b5e093"
    ]


def test_model_license_evidence_is_the_fixture_spdx_id(
    fixture_checks: dict[str, Check],
) -> None:
    assert fixture_checks["g7-model-license"].evidence == ["Apache-2.0"]


def test_model_card_evidence_is_the_fixture_architecture(
    fixture_checks: dict[str, Check],
) -> None:
    """v2 (#306): g7-model-card gained an evidencePath — architectureFamily //
    modelArchitecture // "documented". The bert fixture carries
    modelParameters.modelArchitecture == "bert"."""
    assert fixture_checks["g7-model-card"].evidence == ["bert"]


def test_ds_name_evidence_is_the_fixture_dataset_names(
    fixture_checks: dict[str, Check],
) -> None:
    """v2 (#306): g7-ds-name gained an evidencePath — the dataset names found
    by the `..` descent (modelCard.modelParameters.datasets), unique/sorted."""
    assert fixture_checks["g7-ds-name"].evidence == ["bookcorpus", "wikipedia"]


def test_unsatisfied_evidence_elements_carry_no_evidence(
    fixture_checks: dict[str, Check],
) -> None:
    # hash-alg / openness are warn on the fixture — no evidence extracted.
    assert fixture_checks["g7-model-hash-alg"].evidence is None
    assert fixture_checks["g7-model-openness"].evidence is None


def test_evidence_clamp_bounds_adversarial_flood() -> None:
    """50 openness properties × 1000-char values must clamp to ≤ 8 × 200."""
    doc = {
        "components": [
            {
                "type": "machine-learning-model",
                "name": "m",
                "properties": [
                    {"name": f"openness:key-{i:02d}", "value": "x" * 1000}
                    for i in range(50)
                ],
            }
        ]
    }
    checks = {c.id: c for c in g7.evaluate_g7(doc)}
    openness = checks["g7-model-openness"]
    assert openness.status == "pass"
    assert openness.evidence is not None
    assert len(openness.evidence) == 8
    assert all(len(v) <= 200 for v in openness.evidence)


def test_evidence_is_nul_and_control_char_sanitised() -> None:
    """F-1 regression: evidence is verbatim SBOM content — an embedded NUL
    would abort the verdict's JSONB persist with a Postgres DataError (whole
    ingest fails, psycopg error leaks into scan.error_message). The real
    fixture is cloned and its ML component's purl AND name are poisoned with
    NUL + ESC; the extracted evidence must carry neither."""
    doc = _fixture_doc()
    ml = doc["components"][0]
    ml["purl"] = ml["purl"] + "\x00\x1b[31m"
    ml["name"] = "bert-base\x00-uncased\x1b"
    checks = {c.id: c for c in g7.evaluate_g7(doc)}
    evidence = checks["g7-model-id"].evidence
    assert evidence is not None
    assert evidence == ["pkg:huggingface/google-bert/bert-base-uncased@86b5e093[31m"]
    assert all("\x00" not in v and "\x1b" not in v for v in evidence)
    # And the persisted shape is clean too (as_dict is the JSONB boundary).
    dumped = json.dumps(checks["g7-model-id"].as_dict())
    assert "\\u0000" not in dumped and "\\u001b" not in dumped
    # missing[] carries the poisoned model NAME (per-model coverage offender,
    # registry v2) — in-memory it is verbatim, but the persist boundary cleans
    # it (Check.as_dict runs missing[] through sanitize_jsonb_text).
    assert checks["g7-model-hash-value"].missing == ["bert-base\x00-uncased\x1b"]
    dumped_missing = json.dumps(checks["g7-model-hash-value"].as_dict())
    assert "\\u0000" not in dumped_missing and "\\u001b" not in dumped_missing


def test_evidence_is_deduplicated_and_sorted() -> None:
    """Mirrors jq ``unique`` — duplicates collapse, output sorted."""
    doc = {
        "components": [
            {"type": "machine-learning-model", "name": "a", "purl": "pkg:x/b@1"},
            {"type": "machine-learning-model", "name": "b", "purl": "pkg:x/a@1"},
            {"type": "machine-learning-model", "name": "c", "purl": "pkg:x/b@1"},
        ]
    }
    checks = {c.id: c for c in g7.evaluate_g7(doc)}
    assert checks["g7-model-id"].evidence == ["pkg:x/a@1", "pkg:x/b@1"]


# ---------------------------------------------------------------------------
# Registry v2 (#306) — per-model coverage (missingPath) semantics.
# ---------------------------------------------------------------------------
def test_multi_model_unlicensed_model_is_no_longer_hidden() -> None:
    """THE v2 core fix: v1's any-model semantics passed g7-model-license as
    long as ONE model carried a license, hiding an unlicensed model in a
    multi-model supplier SBOM. The real fixture is cloned and a second model
    WITHOUT licenses is appended → warn "1/2" with the offender named."""
    doc = _fixture_doc()
    doc["components"].append(
        {
            "type": "machine-learning-model",
            "bom-ref": "pkg:huggingface/acme/toxic-lm@1",
            "name": "toxic-lm",
            "version": "1",
            "purl": "pkg:huggingface/acme/toxic-lm@1",
        }
    )
    checks = {c.id: c for c in g7.evaluate_g7(doc)}

    lic = checks["g7-model-license"]
    assert lic.status == "warn"
    assert lic.detail == "1/2 model component(s)"
    assert lic.missing == ["toxic-lm"]
    # Not a pass — no evidence row (evidence is extracted on pass only).
    assert lic.evidence is None

    # Elements BOTH models satisfy stay pass, now counting 2/2.
    name = checks["g7-model-name"]
    assert name.status == "pass"
    assert name.detail == "2/2 model component(s)"
    assert name.missing == []

    # Elements NEITHER model satisfies list both offenders in document order.
    hashes = checks["g7-model-hash-value"]
    assert hashes.status == "warn"
    assert hashes.detail == "0/2 model component(s)"
    assert hashes.missing == [FIXTURE_MODEL, "toxic-lm"]


def test_missingpath_elements_without_models_warn_no_models() -> None:
    """Defensive branch: the evaluator is only wired in for ML-BOMs, but the
    pure module may be handed any document — BomLens fold wording."""
    checks = {c.id: c for c in g7.evaluate_g7({"components": []})}
    for element_id in sorted(g7.missing_element_ids()):
        check = checks[element_id]
        assert check.status == "warn"
        assert check.detail == "no machine-learning-model components"
        assert check.missing == []


def test_missing_list_is_clamped_against_adversarial_flood() -> None:
    """50 unnamed-license models × 1000-char names must clamp to ≤ 8 × 200
    (BomLens caps at MISSING_CAP=50; the port clamps tighter — same JSONB
    posture as evidence)."""
    doc = {
        "components": [
            {"type": "machine-learning-model", "name": f"model-{i:02d}" + "x" * 1000}
            for i in range(50)
        ]
    }
    checks = {c.id: c for c in g7.evaluate_g7(doc)}
    lic = checks["g7-model-license"]
    assert lic.status == "warn"
    assert lic.detail == "0/50 model component(s)"
    assert len(lic.missing) == 8
    assert all(len(n) <= 200 for n in lic.missing)
    # Document order is preserved (no dedupe/sort — mirrors the BomLens fold).
    assert [n[:8] for n in lic.missing] == [f"model-0{i}" for i in range(8)]


def test_unnamed_offending_model_is_labelled_unnamed() -> None:
    """jq offender label ``(.name // "(unnamed)")``."""
    doc = {"components": [{"type": "machine-learning-model", "version": "1"}]}
    checks = {c.id: c for c in g7.evaluate_g7(doc)}
    assert checks["g7-model-license"].missing == ["(unnamed)"]
    assert checks["g7-model-name"].missing == ["(unnamed)"]


def test_openness_prose_declaration_anywhere_satisfies_the_element() -> None:
    """v2 (#306): g7-model-openness accepts a prose openness declaration
    anywhere in the SBOM (`.. | strings`), not only openness:* properties —
    supplier SBOMs that state openness in text still count."""
    doc = _fixture_doc()
    assert {c.id: c for c in g7.evaluate_g7(doc)}[
        "g7-model-openness"
    ].status == "warn"  # baseline: the fixture has no openness signal
    doc["metadata"]["component"]["description"] = (
        "Open-weight release of bert-base-uncased"
    )
    checks = {c.id: c for c in g7.evaluate_g7(doc)}
    openness = checks["g7-model-openness"]
    assert openness.status == "pass"
    assert openness.detail == "present"
    assert openness.evidence == ["Open-weight release of bert-base-uncased"]


# ---------------------------------------------------------------------------
# Adversarial shapes — predicates must degrade to a verdict, never raise.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "doc",
    [
        # 1. empty document
        {},
        # 2. components not an array
        {"components": "notalist", "metadata": {}},
        # 3. component entries that are not objects
        {"components": [None, 1, "x", [], True]},
        # 4. ML component with scalars where objects/arrays are expected
        {
            "components": [
                {
                    "type": "machine-learning-model",
                    "modelCard": "scalar",
                    "licenses": "x",
                    "hashes": {"alg": 1},
                    "properties": "nope",
                    "externalReferences": 7,
                    "name": 3,
                    "version": None,
                }
            ]
        },
        # 5. metadata scalar + dependencies object (not array)
        {"metadata": "scalar", "dependencies": {"ref": "x"}, "version": False},
        # 6. hostile metadata internals (tools scalar, component scalar,
        #    properties object)
        {
            "metadata": {
                "tools": 42,
                "component": "notadict",
                "properties": {"name": 1},
                "authors": "x",
            }
        },
        # 7. data component with scalar componentData + null license entries,
        #    vulnerabilities scalar
        {
            "components": [
                {"type": "data", "componentData": 5, "licenses": [None], "properties": [3]}
            ],
            "vulnerabilities": "nope",
            "externalReferences": [None, 5, {"type": 9}],
        },
        # 8. datasets abuse for the recursive-descent port + signature False
        {
            "a": {"datasets": 5},
            "b": {"datasets": [1, None, {"componentData": "x"}, {"name": 7}]},
            "signature": False,
            "components": [{"type": "machine-learning-model", "modelCard": {"modelParameters": 3}}],
        },
    ],
)
def test_adversarial_docs_never_raise_and_emit_full_catalogue(doc: dict[str, Any]) -> None:
    checks = g7.evaluate_g7(doc)
    assert len(checks) == 51
    assert all(c.status in {"pass", "warn"} for c in checks)
    assert all(c.required is False for c in checks)


def test_deeply_nested_document_is_depth_guarded() -> None:
    """The `..` descent port stops at its ceiling instead of blowing the stack."""
    inner: dict[str, Any] = {"datasets": ["deep"]}
    doc: dict[str, Any] = inner
    for _ in range(300):
        doc = {"wrap": doc}
    checks = {c.id: c for c in g7.evaluate_g7(doc)}
    # Beyond the guard depth the dataset name is simply not found → warn.
    assert checks["g7-ds-name"].status == "warn"


def test_predicate_exception_is_caught_as_evaluation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(doc: dict[str, Any]) -> bool:
        raise RuntimeError("hostile shape reached a predicate")

    monkeypatch.setitem(g7._PREDICATES, "g7-meta-version", _boom)
    checks = {c.id: c for c in g7.evaluate_g7(_fixture_doc())}
    assert checks["g7-meta-version"].status == "warn"
    assert checks["g7-meta-version"].detail == "evaluation error"
    # The rest of the catalogue is unaffected.
    assert checks["g7-meta-timestamp"].status == "pass"


def test_missing_port_exception_is_caught_as_evaluation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(models: list[dict[str, Any]]) -> list[str]:
        raise RuntimeError("hostile shape reached a missingPath port")

    monkeypatch.setitem(g7._MISSING, "g7-model-license", _boom)
    checks = {c.id: c for c in g7.evaluate_g7(_fixture_doc())}
    assert checks["g7-model-license"].status == "warn"
    assert checks["g7-model-license"].detail == "evaluation error"
    assert checks["g7-model-license"].missing == []
    # The rest of the models cluster is unaffected.
    assert checks["g7-model-name"].status == "pass"
    assert checks["g7-model-name"].detail == "1/1 model component(s)"


def test_evidence_exception_keeps_pass_but_drops_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(doc: dict[str, Any]) -> list[str]:
        raise RuntimeError("hostile shape reached an extractor")

    monkeypatch.setitem(g7._EVIDENCE, "g7-model-id", _boom)
    checks = {c.id: c for c in g7.evaluate_g7(_fixture_doc())}
    assert checks["g7-model-id"].status == "pass"
    assert checks["g7-model-id"].evidence is None


# ---------------------------------------------------------------------------
# Serialisation — G7 extension keys present, core Check shape untouched.
# ---------------------------------------------------------------------------
def test_g7_check_as_dict_carries_extension_keys(
    fixture_checks: dict[str, Check],
) -> None:
    d = fixture_checks["g7-model-id"].as_dict()
    assert d["cluster"] == "models"
    assert d["source"] == "auto"
    assert d["role"] == "sbom-author"
    assert d["evidence"] == ["pkg:huggingface/google-bert/bert-base-uncased@86b5e093"]


def test_as_dict_omits_evidence_when_absent(fixture_checks: dict[str, Check]) -> None:
    d = fixture_checks["g7-meta-author"].as_dict()  # warn — no evidence element
    assert "evidence" not in d
    assert d["cluster"] == "metadata"
