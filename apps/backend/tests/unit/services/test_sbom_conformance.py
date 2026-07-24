"""
Unit tests for ``services.sbom_conformance`` — received-SBOM quality scoring.

Pure-function tests: no DB / redis, so they run in the local lane too. Real
tool-output fixtures (cdxgen / syft, recorded under ``tests/fixtures/sbom/``)
exercise realistic density (CLAUDE.md §2 rule 3 — no hand-built minimal SBOMs
for the boundary cases); crafted inputs cover adversarial / threshold edges.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import sbom_conformance as sc

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sbom"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ---------------------------------------------------------------------------
# Format detection.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fixture,expected_format",
    [
        ("real_cyclonedx_small.json", sc.FORMAT_CYCLONEDX),
        ("real_cyclonedx.json", sc.FORMAT_CYCLONEDX),
        ("real_spdx.json", sc.FORMAT_SPDX_JSON),
        ("real_spdx.tag", sc.FORMAT_SPDX_TV),
    ],
)
def test_detect_format_on_real_tool_output(fixture: str, expected_format: str) -> None:
    fmt, _ = sc.detect_format(_load(fixture))
    assert fmt == expected_format


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json at all",
        b"{ this is : broken json ",
        b"<xml>nope</xml>",
        b"null",
        b"[]",  # JSON array, not an object
        b'{"foo": "bar"}',  # object but no SBOM markers
    ],
)
def test_unrecognised_inputs_are_unknown(raw: bytes) -> None:
    fmt, _ = sc.detect_format(raw)
    assert fmt == sc.FORMAT_UNKNOWN
    result = sc.evaluate(raw)
    assert result.result == "fail"
    # Unknown format yields a single mandatory "format" failure, never raises.
    assert result.n_fail == 1


# ---------------------------------------------------------------------------
# Real-output scoring.
# ---------------------------------------------------------------------------
def test_conformant_cyclonedx_passes() -> None:
    """A real npm cdxgen SBOM (purl + graph + licenses + hashes) scores pass."""
    result = sc.evaluate(_load("real_cyclonedx_small.json"))
    assert result.source_format == sc.FORMAT_CYCLONEDX
    assert result.result == "pass"
    assert result.n_fail == 0
    assert result.component_count > 0
    assert result.purl_coverage_pct == 100
    by_id = {c.id: c for c in result.checks}
    assert by_id["transitive"].status == "pass"
    assert by_id["purl"].status == "pass"


def test_dependency_free_cyclonedx_fails_transitive() -> None:
    """A real Python cdxgen SBOM has full PURLs but no dependency graph."""
    result = sc.evaluate(_load("real_cyclonedx.json"))
    by_id = {c.id: c for c in result.checks}
    assert by_id["transitive"].status == "fail"
    assert result.result == "fail"


def test_spdx_json_real_output_scored() -> None:
    result = sc.evaluate(_load("real_spdx.json"))
    assert result.source_format == sc.FORMAT_SPDX_JSON
    # syft dir scan carries PURLs but no DEPENDS_ON graph → mandatory fail.
    by_id = {c.id: c for c in result.checks}
    assert by_id["transitive"].status == "fail"
    assert result.purl_coverage_pct is not None


def test_spdx_tag_value_real_output_scored() -> None:
    result = sc.evaluate(_load("real_spdx.tag"))
    assert result.source_format == sc.FORMAT_SPDX_TV
    by_id = {c.id: c for c in result.checks}
    # Tag-Value is presence-based; coverage percentages are not computed.
    assert result.purl_coverage_pct is None
    assert by_id["timestamp"].status == "pass"


# ---------------------------------------------------------------------------
# Threshold env knobs are read at call time (CLAUDE.md §11).
# ---------------------------------------------------------------------------
def test_purl_threshold_env_override_changes_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _load("real_spdx.json")  # 96% PURL coverage
    # Default min is 90 → purl check passes.
    monkeypatch.delenv("SBOM_CONFORMANCE_PURL_MIN_PCT", raising=False)
    base = {c.id: c for c in sc.evaluate(raw).checks}
    assert base["purl"].status == "pass"
    # Raise the bar above the observed coverage → purl check now fails.
    monkeypatch.setenv("SBOM_CONFORMANCE_PURL_MIN_PCT", "99")
    tightened = {c.id: c for c in sc.evaluate(raw).checks}
    assert tightened["purl"].status == "fail"


def test_recommended_only_miss_yields_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """All mandatory pass + a recommended miss → top-level 'warn', not 'fail'."""
    # Build from the conformant fixture but demand 100% hash coverage so the
    # (recommended) hash check warns while every mandatory check still passes.
    raw = _load("real_cyclonedx_small.json")
    monkeypatch.setenv("SBOM_CONFORMANCE_HASH_MIN_PCT", "101")
    result = sc.evaluate(raw)
    by_id = {c.id: c for c in result.checks}
    assert by_id["hash"].status == "warn"
    assert result.n_fail == 0
    assert result.result == "warn"


# ---------------------------------------------------------------------------
# Adversarial / hostile CycloneDX shapes (crafted — boundary of the parser).
# ---------------------------------------------------------------------------
def _cdx(components: list[dict], **extra: object) -> bytes:
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {"timestamp": "2026-01-01T00:00:00Z", "tools": [{"name": "t"}],
                     "component": {"name": "root", "version": "1.0"}},
        "components": components,
        "dependencies": [{"ref": "a", "dependsOn": ["b"]}],
    }
    doc.update(extra)
    return json.dumps(doc).encode()


def test_pkg_generic_abuse_fails_no_generic() -> None:
    raw = _cdx([
        {"name": "x", "version": "1", "purl": "pkg:generic/x@1"},
        {"name": "y", "version": "1", "purl": "pkg:npm/y@1"},
    ])
    by_id = {c.id: c for c in sc.evaluate(raw).checks}
    assert by_id["no-generic"].status == "fail"
    assert "x" in by_id["no-generic"].missing[0] or by_id["no-generic"].missing


def test_missing_version_fails_name_version() -> None:
    raw = _cdx([{"name": "x", "purl": "pkg:npm/x"}])  # no version
    by_id = {c.id: c for c in sc.evaluate(raw).checks}
    assert by_id["name-version"].status == "fail"


def test_tools_object_form_counted() -> None:
    """CycloneDX 1.5+ shapes metadata.tools as an object with components[]."""
    raw = _cdx(
        [{"name": "x", "version": "1", "purl": "pkg:npm/x@1"}],
        metadata={
            "timestamp": "2026-01-01T00:00:00Z",
            "tools": {"components": [{"name": "cdxgen"}]},
            "component": {"name": "r", "version": "1"},
        },
    )
    by_id = {c.id: c for c in sc.evaluate(raw).checks}
    assert by_id["tools"].status == "pass"


def test_missing_list_capped_at_50() -> None:
    comps = [{"name": f"c{i}", "version": "1"} for i in range(120)]  # all no-purl
    by_id = {c.id: c for c in sc.evaluate(_cdx(comps)).checks}
    assert by_id["purl"].status == "fail"
    assert len(by_id["purl"].missing) == 50


@pytest.mark.parametrize(
    "raw",
    [
        b'{"bomFormat":"CycloneDX","specVersion":"1.6"}',  # no components key
        b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":"notalist"}',
        b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":[null,1,"x"]}',
        b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":[],"x":" \r\n"}',
    ],
)
def test_degenerate_cyclonedx_never_raises(raw: bytes) -> None:
    result = sc.evaluate(raw)  # must not raise
    assert result.result in {"pass", "warn", "fail"}


def test_empty_components_zero_division_guarded() -> None:
    """Empty denominator reads as "nothing to measure", not "0%, the worst
    score" (BomLens #457 parity): a zero-package SBOM must not fail the
    mandatory purl check for a field that had no subject."""
    raw = _cdx([])
    result = sc.evaluate(raw)
    by_id = {c.id: c for c in result.checks}
    assert by_id["purl"].status == "pass"
    assert by_id["purl"].detail == "no packages to measure"
    assert by_id["component-creator"].status == "pass"
    assert by_id["component-creator"].detail == "no packages to measure"
    assert by_id["hash-algorithm"].status == "pass"
    assert by_id["hash-algorithm"].detail == "nothing to measure"
    assert result.purl_coverage_pct == 0


def test_spdx_json_empty_packages_purl_guarded() -> None:
    """Same empty-denominator guard on the SPDX-JSON path (upstream applied
    it to both JSON scorers; Tag-Value was already consistent)."""
    raw = json.dumps(
        {
            "spdxVersion": "SPDX-2.3",
            "name": "empty-doc",
            "creationInfo": {"created": "2026-01-01T00:00:00Z", "creators": ["Tool: x"]},
            "packages": [],
        }
    ).encode()
    result = sc.evaluate(raw)
    by_id = {c.id: c for c in result.checks}
    assert by_id["purl"].status == "pass"
    assert by_id["purl"].detail == "no packages to measure"


# ---------------------------------------------------------------------------
# Catalogue invariant: every CHECK_ID is emitted for a recognised format, so
# the FE mirror constant (contract test) can rely on the full set.
# ---------------------------------------------------------------------------
def test_recognised_format_emits_full_check_catalogue() -> None:
    ids = {c.id for c in sc.evaluate(_load("real_cyclonedx_small.json")).checks}
    assert ids == set(sc.CHECK_IDS)


# ---------------------------------------------------------------------------
# G7 AI SBOM advisory checks (services.g7_conformance) — appended ONLY when a
# machine-learning-model component exists, and NEVER moving the core verdict.
# The 51-element per-check expectations live in test_g7_conformance.py; here
# we lock the integration seam.
# ---------------------------------------------------------------------------
AIBOM_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "sbom_ingest"
    / "aibom-owasp-1_7.json"
)


def test_non_ml_1_7_document_emits_no_g7_checks() -> None:
    """A 1.7 document WITHOUT an ML component gets only the core checks."""
    raw = _cdx(
        [{"type": "library", "name": "x", "version": "1", "purl": "pkg:npm/x@1"}],
        specVersion="1.7",
    )
    result = sc.evaluate(raw)
    assert {c.id for c in result.checks} == set(sc.CHECK_IDS)
    assert not [c for c in result.checks if c.id.startswith("g7-")]


def test_ml_bom_appends_51_advisory_g7_checks() -> None:
    result = sc.evaluate(AIBOM_FIXTURE.read_bytes())
    core = [c for c in result.checks if c.cluster is None]
    g7_checks = [c for c in result.checks if c.cluster is not None]
    assert {c.id for c in core} == set(sc.CHECK_IDS)
    assert len(g7_checks) == 51
    assert len(result.checks) == len(sc.CHECK_IDS) + 51
    assert all(c.id.startswith("g7-") for c in g7_checks)
    assert all(not c.required for c in g7_checks)
    assert all(c.status in {"pass", "warn"} for c in g7_checks)


def test_g7_warns_never_move_the_core_verdict_or_counters() -> None:
    """AIBOM fixture: every mandatory core check passes; the recommended core
    hash check warns → result 'warn' with n_warn == 1 EVEN THOUGH dozens of
    G7 advisory checks also warn (they are excluded from the aggregation)."""
    result = sc.evaluate(AIBOM_FIXTURE.read_bytes())
    g7_warns = [c for c in result.checks if c.cluster is not None and c.status == "warn"]
    assert g7_warns, "the fixture must carry warning G7 elements for this lock"
    assert result.n_fail == 0
    assert result.n_warn == 1  # the core hash check only
    assert result.result == "warn"


def test_all_core_pass_stays_pass_despite_g7_warns() -> None:
    """A fully-conformant core document whose only component is an ML model:
    the overall result stays 'pass' regardless of absent G7 elements."""
    raw = _cdx(
        [
            {
                "type": "machine-learning-model",
                "name": "m",
                "version": "1",
                "purl": "pkg:huggingface/org/m@1",
                "licenses": [{"license": {"id": "MIT"}}],
                "hashes": [{"alg": "SHA-256", "content": "aa"}],
            }
        ]
    )
    result = sc.evaluate(raw)
    assert any(c.cluster is not None and c.status == "warn" for c in result.checks)
    assert result.n_fail == 0
    assert result.n_warn == 0
    assert result.result == "pass"


def test_core_check_as_dict_is_byte_compatible() -> None:
    """Backwards compatibility: the 9 core checks serialise with EXACTLY the
    legacy key set — no new keys leak into persisted rows for non-ML SBOMs."""
    result = sc.evaluate(_load("real_cyclonedx_small.json"))
    for check in result.checks:
        # ``file-properties`` is the one core check that legitimately carries
        # ``source`` ("auto"/"na" — upstream parity: it marks whether any
        # producer inspected the delivered files). Every other core check
        # keeps the exact legacy key set.
        expected = {"id", "label", "required", "status", "detail", "missing"}
        if check.id == "file-properties":
            expected |= {"source"}
        assert set(check.as_dict()) == expected


def test_g7_check_as_dict_carries_extension_fields() -> None:
    result = sc.evaluate(AIBOM_FIXTURE.read_bytes())
    by_id = {c.id: c for c in result.checks}
    d = by_id["g7-model-id"].as_dict()
    assert d["cluster"] == "models"
    assert d["source"] == "auto"
    assert d["role"] == "sbom-author"
    assert d["evidence"] == ["pkg:huggingface/google-bert/bert-base-uncased@86b5e093"]
    # Unsatisfied element: extension metadata present, evidence omitted.
    absent = by_id["g7-model-openness"].as_dict()
    assert absent["cluster"] == "models"
    assert "evidence" not in absent


def test_as_dict_sanitises_sbom_derived_strings() -> None:
    """F-1: ``as_dict`` is the JSONB persist boundary — SBOM-derived strings in
    ``detail`` (timestamp, top-component name) and ``missing[]`` (component
    names) must be NUL / control-char cleaned. Pre-fix an embedded NUL reached
    the ``sbom_conformance.checks`` JSONB as ``\\u0000``, Postgres raised a
    DataError, the WHOLE ingest scan failed, and the raw psycopg message
    surfaced in the user-visible ``scan.error_message``."""
    raw = _cdx(
        [{"name": "evil\x00comp\x1b", "purl": None}],  # no version → missing[]
        metadata={
            "timestamp": "2026-01-01T00:00:00Z\x00\x1b[2J",
            "tools": [{"name": "t"}],
            "component": {"name": "root\x00", "version": "1.0"},
        },
    )
    result = sc.evaluate(raw)
    dumped = [c.as_dict() for c in result.checks]
    blob = json.dumps(dumped)
    assert "\\u0000" not in blob and "\\u001b" not in blob
    by_id = {d["id"]: d for d in dumped}
    assert by_id["timestamp"]["detail"] == "2026-01-01T00:00:00Z[2J"
    assert by_id["top-component"]["detail"] == "root@1.0"
    assert by_id["name-version"]["missing"] == ["evilcomp"]
    # The in-memory dataclass keeps the raw value; only serialisation cleans.
    raw_detail = next(c for c in result.checks if c.id == "timestamp").detail
    assert "\x00" in raw_detail


# ---------------------------------------------------------------------------
# Regulatory field checks (BomLens #462 parity — BSI TR-03183-2 / NTIA).
# Advisory AND verdict-neutral: they inform the regulatory crosswalk, never
# the submission verdict.
# ---------------------------------------------------------------------------
_COMPLETE_PKG = {
    "type": "library",
    "name": "x",
    "version": "1",
    "purl": "pkg:npm/x@1",
    "licenses": [{"license": {"id": "MIT"}}],
    "hashes": [{"alg": "SHA-256", "content": "aa"}],
}


def test_regulatory_field_checks_are_verdict_neutral() -> None:
    """A document whose core checks all pass stays ``pass`` even though the
    regulatory field checks all warn (no SHA-512 / creator / filename / URI
    anywhere) — the upstream contract: they describe how well the SBOM would
    answer a regulator, and never move the submission verdict."""
    result = sc.evaluate(_cdx([dict(_COMPLETE_PKG)]))
    by_id = {c.id: c for c in result.checks}
    for check_id in sorted(sc.REGULATORY_FIELD_CHECK_IDS):
        assert by_id[check_id].status == "warn", check_id
        assert by_id[check_id].required is False, check_id
    assert result.n_fail == 0
    assert result.n_warn == 0
    assert result.result == "pass"


def test_regulatory_field_checks_score_full_coverage() -> None:
    rich = dict(
        _COMPLETE_PKG,
        hashes=[{"alg": "SHA-512", "content": "bb"}],
        supplier={"name": "Acme", "url": ["https://acme.example"]},
        externalReferences=[{"type": "vcs", "url": "https://git.example/x"}],
        properties=[
            {"name": "bsi:component:filename", "value": "x-1.jar"},
            {"name": "bsi:component:executable", "value": "false"},
            {"name": "bsi:component:archive", "value": "true"},
            {"name": "bsi:component:structured", "value": "true"},
        ],
    )
    by_id = {c.id: c for c in sc.evaluate(_cdx([rich])).checks}
    assert by_id["hash-algorithm"].status == "pass"
    assert by_id["component-creator"].status == "pass"
    assert by_id["component-filename"].status == "pass"
    assert by_id["artifact-uri"].status == "pass"
    assert by_id["file-properties"].status == "pass"
    assert by_id["file-properties"].source == "auto"
    assert by_id["component-creator"].detail == "100% (1/1)"


def test_file_properties_without_producer_reads_as_review() -> None:
    """No producer in the chain inspected the delivered files → the check is a
    human-review item (source "na"), with a detail that says why, not a bare
    coverage number."""
    by_id = {c.id: c for c in sc.evaluate(_cdx([dict(_COMPLETE_PKG)])).checks}
    fp = by_id["file-properties"]
    assert fp.source == "na"
    assert fp.status == "warn"
    assert "requires inspecting the delivered files" in fp.detail


def test_data_components_excluded_from_package_scoped_checks() -> None:
    """A dataset (`type: "data"`) has no purl type, no version, no filename to
    carry (BomLens #456 parity): package-scoped checks must not fail an
    otherwise complete ML-BOM for fields that cannot exist. License and
    checksum coverage still count it, because those it can carry."""
    dataset = {
        "type": "data",
        "name": "wikipedia",
        "licenses": [{"license": {"id": "CC-BY-SA-4.0"}}],
        "hashes": [{"alg": "SHA-512", "content": "cc"}],
    }
    result = sc.evaluate(_cdx([dict(_COMPLETE_PKG), dataset]))
    by_id = {c.id: c for c in result.checks}
    assert by_id["name-version"].status == "pass"
    assert by_id["name-version"].detail == "1/1"
    assert by_id["purl"].status == "pass"
    assert by_id["purl"].detail == "100% (1/1)"
    # license/hash count both components; the dataset's SHA-512 counts too.
    assert by_id["license"].detail == "100% (2/2)"
    assert by_id["hash-algorithm"].detail == "50% (1/2)"
    assert result.component_count == 2


# ---------------------------------------------------------------------------
# Hostile container shapes (security-reviewer Medium, this branch): every
# nested container can arrive as a scalar — ``evaluate`` must never raise.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        # Reviewer's exact repro: scalar where a list/dict belongs.
        b'{"bomFormat":"CycloneDX","specVersion":"1.5",'
        b'"components":[{"name":"a","version":"1","hashes":1}]}',
        b'{"bomFormat":"CycloneDX","specVersion":"1.5",'
        b'"components":[{"name":"a","version":"1","externalReferences":7}]}',
        b'{"bomFormat":"CycloneDX","specVersion":"1.5",'
        b'"components":[{"name":"a","version":"1","properties":7}]}',
        # Pre-existing gaps closed alongside (same helper).
        b'{"bomFormat":"CycloneDX","specVersion":"1.5","metadata":"x"}',
        b'{"bomFormat":"CycloneDX","specVersion":"1.5","dependencies":5}',
        b'{"bomFormat":"CycloneDX","specVersion":"1.5","components":5}',
        b'{"bomFormat":"CycloneDX","specVersion":"1.5",'
        b'"metadata":{"component":"x","tools":{"components":3}}}',
        b'{"bomFormat":"CycloneDX","specVersion":"1.5",'
        b'"dependencies":[{"ref":"a","dependsOn":9}]}',
        # SPDX-JSON siblings.
        b'{"spdxVersion":"SPDX-2.3","creationInfo":"x","packages":5}',
        b'{"spdxVersion":"SPDX-2.3","creationInfo":{"creators":3},'
        b'"packages":[{"name":"a","versionInfo":"1","externalRefs":7}],'
        b'"relationships":5,"documentDescribes":5}',
    ],
)
def test_hostile_scalar_containers_never_raise(raw: bytes) -> None:
    result = sc.evaluate(raw)  # must not raise — a bad SBOM fails, not the task
    assert result.result in {"pass", "warn", "fail"}


def test_all_data_components_cannot_evade_mandatory_checks() -> None:
    """Anti-evasion (security-reviewer Low): typing every component "data"
    zeroes the package denominator — the mandatory name-version / purl checks
    must degrade to warn, not slide to pass on the empty-denominator guard."""
    raw = _cdx(
        [
            {"type": "data", "name": "not-really-a-dataset"},
            {"type": "data", "name": "also-a-library-honest"},
        ]
    )
    result = sc.evaluate(raw)
    by_id = {c.id: c for c in result.checks}
    assert by_id["name-version"].status == "warn"
    assert by_id["purl"].status == "warn"
    assert 'typed "data"' in by_id["purl"].detail
    assert result.result == "warn"
    assert result.n_fail == 0
