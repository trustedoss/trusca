"""
Unit tests for ``services.sbom_convert`` — SBOM → CycloneDX-dict normalisation.

The output must be consumable by ``tasks.scan_source.persist_sbom_components``:
components carry ``purl`` / ``name`` / ``version`` / ``bom-ref`` and a
``licenses`` array in the ``[{"license": {"id": ...}}]`` /
``[{"expression": ...}]`` shape ``_extract_spdx_ids`` reads; ``dependencies``
carry the ``DEPENDS_ON`` graph so ``_persist_dependency_graph`` can resolve edges.

Pure-function (no DB / redis). Real syft fixtures cover density; crafted SPDX
inputs cover the license-expression / hostile-input edges.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import sbom_convert as cv

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sbom"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ---------------------------------------------------------------------------
# CycloneDX passthrough.
# ---------------------------------------------------------------------------
def test_cyclonedx_passes_through_unchanged() -> None:
    raw = _load("real_cyclonedx_small.json")
    out = cv.to_cyclonedx(raw)
    assert out == json.loads(raw)
    assert out["bomFormat"] == "CycloneDX"
    assert out["components"]


# ---------------------------------------------------------------------------
# SPDX-JSON → CycloneDX (real syft output).
# ---------------------------------------------------------------------------
def test_spdx_json_real_output_maps_components() -> None:
    out = cv.to_cyclonedx(_load("real_spdx.json"))
    assert out["bomFormat"] == "CycloneDX"
    assert len(out["components"]) > 0
    # PURL-bearing packages carry their purl forward (syft emits purls for ~all).
    with_purl = [c for c in out["components"] if c.get("purl")]
    assert with_purl
    for c in with_purl:
        assert c["name"]
        assert c["purl"].startswith("pkg:")
        assert "bom-ref" in c  # SPDXID preserved for edge resolution


def test_spdx_json_round_trips_through_conformance() -> None:
    """Converting then re-scoring keeps component count stable (no data loss)."""
    from services import sbom_conformance as sc

    raw = _load("real_spdx.json")
    spdx_count = sc.evaluate(raw).component_count
    converted = cv.to_cyclonedx(raw)
    assert len(converted["components"]) == spdx_count


# ---------------------------------------------------------------------------
# SPDX-JSON license + dependency mapping (crafted).
# ---------------------------------------------------------------------------
def _spdx_json(packages: list[dict], relationships: list[dict] | None = None) -> bytes:
    return json.dumps(
        {
            "spdxVersion": "SPDX-2.3",
            "name": "doc",
            "creationInfo": {"created": "2026-01-01T00:00:00Z", "creators": ["Tool: syft"]},
            "packages": packages,
            "relationships": relationships or [],
        }
    ).encode()


def test_single_license_id_emitted_as_license_id() -> None:
    out = cv.to_cyclonedx(_spdx_json([
        {"SPDXID": "SPDXRef-a", "name": "a", "versionInfo": "1",
         "licenseConcluded": "MIT"},
    ]))
    assert out["components"][0]["licenses"] == [{"license": {"id": "MIT"}}]


def test_compound_expression_emitted_as_expression() -> None:
    out = cv.to_cyclonedx(_spdx_json([
        {"SPDXID": "SPDXRef-a", "name": "a", "versionInfo": "1",
         "licenseConcluded": "(MIT OR GPL-3.0-only)"},
    ]))
    assert out["components"][0]["licenses"] == [{"expression": "(MIT OR GPL-3.0-only)"}]


def test_concluded_preferred_over_declared() -> None:
    out = cv.to_cyclonedx(_spdx_json([
        {"SPDXID": "SPDXRef-a", "name": "a", "versionInfo": "1",
         "licenseConcluded": "Apache-2.0", "licenseDeclared": "MIT"},
    ]))
    assert out["components"][0]["licenses"] == [{"license": {"id": "Apache-2.0"}}]


@pytest.mark.parametrize("value", ["NOASSERTION", "NONE", "", "   "])
def test_non_asserted_license_dropped(value: str) -> None:
    out = cv.to_cyclonedx(_spdx_json([
        {"SPDXID": "SPDXRef-a", "name": "a", "versionInfo": "1",
         "licenseConcluded": value, "licenseDeclared": value},
    ]))
    assert "licenses" not in out["components"][0]


def test_depends_on_relationships_become_dependencies() -> None:
    out = cv.to_cyclonedx(_spdx_json(
        packages=[
            {"SPDXID": "SPDXRef-a", "name": "a", "versionInfo": "1",
             "externalRefs": [{"referenceType": "purl", "referenceLocator": "pkg:npm/a@1"}]},
            {"SPDXID": "SPDXRef-b", "name": "b", "versionInfo": "1",
             "externalRefs": [{"referenceType": "purl", "referenceLocator": "pkg:npm/b@1"}]},
        ],
        relationships=[
            {"spdxElementId": "SPDXRef-a", "relatedSpdxElement": "SPDXRef-b",
             "relationshipType": "DEPENDS_ON"},
            {"spdxElementId": "SPDXRef-a", "relatedSpdxElement": "SPDXRef-b",
             "relationshipType": "DEPENDS_ON"},  # duplicate → deduped
            {"spdxElementId": "SPDXRef-a", "relatedSpdxElement": "SPDXRef-x",
             "relationshipType": "CONTAINS"},  # non-DEPENDS_ON ignored
        ],
    ))
    deps = {d["ref"]: d["dependsOn"] for d in out["dependencies"]}
    assert deps == {"SPDXRef-a": ["SPDXRef-b"]}


def test_package_without_name_skipped() -> None:
    out = cv.to_cyclonedx(_spdx_json([
        {"SPDXID": "SPDXRef-a", "versionInfo": "1"},  # no name
        {"SPDXID": "SPDXRef-b", "name": "b", "versionInfo": "1"},
    ]))
    assert [c["name"] for c in out["components"]] == ["b"]


# ---------------------------------------------------------------------------
# SPDX Tag-Value → CycloneDX (real syft output + crafted).
# ---------------------------------------------------------------------------
def test_spdx_tag_value_real_output_maps_components() -> None:
    out = cv.to_cyclonedx(_load("real_spdx.tag"))
    assert out["bomFormat"] == "CycloneDX"
    assert len(out["components"]) > 0
    assert [c for c in out["components"] if c.get("purl")]
    assert all(c.get("name") for c in out["components"])


def test_spdx_tag_value_crafted_parse() -> None:
    doc = (
        b"SPDXVersion: SPDX-2.3\n"
        b"Created: 2026-01-01T00:00:00Z\n"
        b"Creator: Tool: syft-1.0\n"
        b"DocumentName: my-doc\n"
        b"\n"
        b"PackageName: alpha\n"
        b"SPDXID: SPDXRef-alpha\n"
        b"PackageVersion: 1.2.3\n"
        b"PackageLicenseConcluded: MIT\n"
        b"PackageChecksum: SHA1: deadbeef\n"
        b"ExternalRef: PACKAGE-MANAGER purl pkg:npm/alpha@1.2.3\n"
        b"\n"
        b"PackageName: beta\n"
        b"SPDXID: SPDXRef-beta\n"
        b"PackageVersion: 4.5.6\n"
        b"ExternalRef: PACKAGE-MANAGER purl pkg:npm/beta@4.5.6\n"
        b"\n"
        b"Relationship: SPDXRef-alpha DEPENDS_ON SPDXRef-beta\n"
    )
    out = cv.to_cyclonedx(doc)
    by_name = {c["name"]: c for c in out["components"]}
    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"]["version"] == "1.2.3"
    assert by_name["alpha"]["purl"] == "pkg:npm/alpha@1.2.3"
    assert by_name["alpha"]["bom-ref"] == "SPDXRef-alpha"
    assert by_name["alpha"]["licenses"] == [{"license": {"id": "MIT"}}]
    assert by_name["alpha"]["hashes"] == [{"content": "deadbeef"}]
    deps = {d["ref"]: d["dependsOn"] for d in out["dependencies"]}
    assert deps == {"SPDXRef-alpha": ["SPDXRef-beta"]}


def test_spdx_tag_value_crlf_tolerated() -> None:
    doc = (
        b"SPDXVersion: SPDX-2.3\r\n"
        b"Created: 2026-01-01T00:00:00Z\r\n"
        b"PackageName: gamma\r\n"
        b"PackageVersion: 9\r\n"
        b"ExternalRef: PACKAGE-MANAGER purl pkg:pypi/gamma@9\r\n"
    )
    out = cv.to_cyclonedx(doc)
    assert out["components"][0]["name"] == "gamma"
    assert out["components"][0]["purl"] == "pkg:pypi/gamma@9"


# ---------------------------------------------------------------------------
# Unsupported / hostile inputs.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not an sbom",
        b"<rdf:RDF>spdx</rdf:RDF>",
        b'<?xml version="1.0"?><bom/>',  # CycloneDX XML unsupported
        b"\x00\x01\x02binary",
        b'{"foo": 1}',
    ],
)
def test_unsupported_formats_raise(raw: bytes) -> None:
    with pytest.raises(cv.UnsupportedSbomFormat):
        cv.to_cyclonedx(raw)


def test_escaped_unicode_in_json_round_trips() -> None:
    # A CycloneDX doc with a JSON unicode escape is valid JSON and must
    # round-trip through the passthrough path without raising.
    raw = (
        b'{"bomFormat":"CycloneDX","specVersion":"1.6",'
        b'"components":[{"name":"caf\\u00e9","version":"1"}]}'
    )
    out = cv.to_cyclonedx(raw)
    assert out["components"][0]["name"] == "caf\u00e9"
