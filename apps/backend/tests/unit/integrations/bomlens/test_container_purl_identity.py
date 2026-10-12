"""A container package gets the same stored PURL from either container engine.

The in-process container path stores Trivy's PURL without its qualifiers
(``tasks.scan_container._component_identity``); the BomLens path stores the
SBOM's PURL, and syft writes ``?arch=...&distro=...`` on every OS package.
Both now go through ``services.purl_build.purl_without_qualifiers``. These
tests pin that rule against the PURLs both engines recorded for the same
images (``engine-comparison-container-purls.json``, see PROVENANCE.md), and
against the function each path calls. The database half is in
``tests/integration/scan/test_bomlens_container_engine.py``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from services.purl_build import purl_without_qualifiers

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "bomlens"
COMPARISON = json.loads(
    (FIXTURES / "engine-comparison-container-purls.json").read_text(encoding="utf-8")
)
IMAGES = sorted(COMPARISON["images"])


def _bomlens_purls(image: str) -> list[str]:
    purls: list[str] = COMPARISON["images"][image]["bomlens"]["purls"]
    return purls


def _inprocess_purls(image: str) -> set[str]:
    return set(COMPARISON["images"][image]["inprocess"]["purls"])


def test_the_recorded_purls_carry_the_qualifiers_this_is_about() -> None:
    """The fixture is the case: distro, arch and epoch on the BomLens side."""
    bomlens = [p for image in IMAGES for p in _bomlens_purls(image)]
    assert any("distro=debian-12.7" in p for p in bomlens)
    assert any("&epoch=1" in p for p in bomlens)
    assert any(p.startswith("pkg:apk/") and "?arch=x86_64" in p for p in bomlens)
    assert not any("?" in p for image in IMAGES for p in _inprocess_purls(image))


@pytest.mark.parametrize("image", IMAGES)
def test_the_rule_turns_each_bomlens_purl_into_the_in_process_one(image: str) -> None:
    bomlens = _bomlens_purls(image)
    stored = [purl_without_qualifiers(p) for p in bomlens]
    # Every in-process PURL comes out, and nothing else does.
    assert set(stored) == _inprocess_purls(image)
    # No two packages of the image collapse into one.
    assert len(set(stored)) == len(bomlens)


@pytest.mark.parametrize("image", IMAGES)
def test_the_in_process_identity_gives_the_same_purl_for_the_same_package(image: str) -> None:
    """What ``_component_identity`` makes of Trivy's PURL is what BomLens stores."""
    from tasks.scan_container import _component_identity

    for purl in _bomlens_purls(image):
        identity = _component_identity(
            {"PkgIdentifier": {"PURL": purl}},
            ecosystem="debian",
            pkg_name="unused",
            installed="unused",
        )
        assert identity is not None
        stored, component_purl, _package_type = identity
        assert stored == purl_without_qualifiers(purl)
        assert component_purl == stored.rpartition("@")[0]


def test_the_rule_keeps_a_subpath_only_when_there_are_no_qualifiers() -> None:
    assert purl_without_qualifiers("pkg:deb/debian/a@1?arch=all&epoch=1") == "pkg:deb/debian/a@1"
    assert purl_without_qualifiers("pkg:golang/x/y@1?k=v#sub") == "pkg:golang/x/y@1"
    assert purl_without_qualifiers("pkg:golang/x/y@1#sub") == "pkg:golang/x/y@1#sub"
    assert purl_without_qualifiers("pkg:npm/a@1") == "pkg:npm/a@1"


def _image_sbom() -> dict[str, Any]:
    return {
        "components": [
            {
                "bom-ref": "pkg:deb/debian/bsdutils@2.38.1?arch=amd64&distro=debian-12.7"
                "&epoch=1&package-id=1",
                "type": "library",
                "name": "bsdutils",
                "version": "1:2.38.1",
                "purl": "pkg:deb/debian/bsdutils@2.38.1?arch=amd64&distro=debian-12.7&epoch=1",
                "components": [
                    {
                        "bom-ref": "nested",
                        "type": "library",
                        "name": "inner",
                        "version": "1",
                        "purl": "pkg:apk/alpine/inner@1?arch=x86_64&distro=3.17.7",
                    }
                ],
            },
            {
                "type": "library",
                "name": "commons",
                "version": "1",
                "purl": "pkg:maven/org.x/commons@1?type=jar",
            },
        ]
    }


def test_an_image_sbom_reaches_the_persist_path_without_qualifiers() -> None:
    from tasks.bomlens_result import sbom_for_persistence

    sbom = _image_sbom()
    original = copy.deepcopy(sbom)

    shaped, left_out = sbom_for_persistence(sbom, container_image=True)

    assert left_out == 0
    top, maven = shaped["components"]
    assert top["purl"] == "pkg:deb/debian/bsdutils@2.38.1"
    assert top["components"][0]["purl"] == "pkg:apk/alpine/inner@1"
    # The in-process container path drops every qualifier, ?type=jar included.
    assert maven["purl"] == "pkg:maven/org.x/commons@1"
    # The bom-ref keys the dependency graph and is left as written.
    assert top["bom-ref"] == original["components"][0]["bom-ref"]
    # The document on disk stays exactly as the engine wrote and hashed it.
    assert sbom == original


def test_a_source_sbom_keeps_its_qualifiers() -> None:
    """The in-process source path stores cdxgen's PURL as written."""
    from tasks.bomlens_result import sbom_for_persistence

    sbom = _image_sbom()
    shaped, _ = sbom_for_persistence(sbom)

    assert [c["purl"] for c in shaped["components"]] == [c["purl"] for c in sbom["components"]]
    assert shaped["components"][0]["components"][0]["purl"] == (
        "pkg:apk/alpine/inner@1?arch=x86_64&distro=3.17.7"
    )


def test_findings_are_matched_on_the_form_the_image_is_stored_under() -> None:
    """Trivy's PURL for a finding reduces to the stored PURL, so it attaches."""
    from services.vulnerability_matching import _purl_from_identifier

    stored = purl_without_qualifiers(
        "pkg:deb/debian/bsdutils@2.38.1?arch=amd64&distro=debian-12.7&epoch=1"
    )
    vuln = {
        "PkgIdentifier": {
            "PURL": "pkg:deb/debian/bsdutils@2.38.1?arch=amd64&distro=debian-12.7&epoch=1"
        }
    }
    assert _purl_from_identifier(vuln) == stored
