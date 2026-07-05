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
    raw = _cdx([])
    result = sc.evaluate(raw)
    by_id = {c.id: c for c in result.checks}
    assert by_id["purl"].detail.startswith("0%")
    assert result.purl_coverage_pct == 0


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
    """A 1.7 document WITHOUT an ML component gets only the 9 core checks."""
    raw = _cdx(
        [{"type": "library", "name": "x", "version": "1", "purl": "pkg:npm/x@1"}],
        specVersion="1.7",
    )
    result = sc.evaluate(raw)
    assert len(result.checks) == 9
    assert not [c for c in result.checks if c.id.startswith("g7-")]


def test_ml_bom_appends_51_advisory_g7_checks() -> None:
    result = sc.evaluate(AIBOM_FIXTURE.read_bytes())
    core = [c for c in result.checks if c.cluster is None]
    g7_checks = [c for c in result.checks if c.cluster is not None]
    assert {c.id for c in core} == set(sc.CHECK_IDS)
    assert len(g7_checks) == 51
    assert len(result.checks) == 9 + 51
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
        assert set(check.as_dict()) == {
            "id",
            "label",
            "required",
            "status",
            "detail",
            "missing",
        }


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
