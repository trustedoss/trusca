# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for :mod:`services.os_context`.

The module exists because Trivy will not match a distro package without an
operating-system component in the document. That behaviour was measured
against a real Trivy (0.71.2) while writing the module — 0 findings without
the component, 306 with it, on the same five CentOS 7 rpm PURLs — and the
shapes that measurement pinned down are asserted here as contracts:

  * a CycloneDX ``operating-system`` component;
  * an SPDX package whose SPDXID starts with ``SPDXRef-OperatingSystem``
    (``primaryPackagePurpose`` alone is NOT read by Trivy, so a document
    carrying only that still needs one added).

Changing either shape silently returns the pipeline to reporting zero
vulnerabilities on a document full of vulnerable packages, which is why they
are asserted literally rather than through the module's own constants.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from services.os_context import (
    OsContext,
    classify_purl,
    enrich_sbom_bytes,
    infer_os_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cdx(*purls: str, extra: list[dict[str, Any]] | None = None) -> bytes:
    components: list[dict[str, Any]] = [
        {"type": "library", "name": f"pkg{i}", "version": "1", "purl": purl}
        for i, purl in enumerate(purls)
    ]
    components.extend(extra or [])
    return json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "components": components,
        }
    ).encode()


def _spdx(*purls: str, extra: list[dict[str, Any]] | None = None) -> bytes:
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": f"SPDXRef-Package-{i}",
            "name": f"pkg{i}",
            "versionInfo": "1",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": purl,
                }
            ],
        }
        for i, purl in enumerate(purls)
    ]
    packages.extend(extra or [])
    return json.dumps(
        {
            "spdxVersion": "SPDX-2.3",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "doc",
            "packages": packages,
        }
    ).encode()


def _os_components(document: bytes) -> list[dict[str, Any]]:
    doc = json.loads(document)
    return [c for c in doc["components"] if c.get("type") == "operating-system"]


def _os_packages(document: bytes) -> list[dict[str, Any]]:
    doc = json.loads(document)
    return [
        p
        for p in doc["packages"]
        if str(p.get("SPDXID", "")).startswith("SPDXRef-OperatingSystem")
    ]


# ---------------------------------------------------------------------------
# classify_purl — where a version may be read from, and where it may not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("purl", "expected"),
    [
        # rpm: the .elN release suffix carries the major.
        ("pkg:rpm/centos/openssl@1.0.2k-19.el7?arch=x86_64", ("centos", "7")),
        ("pkg:rpm/rocky/glibc@2.28-236.el8_9.12?arch=x86_64", ("rocky", "8")),
        # rpm: a distro= qualifier wins over the namespace, and stops at the dot.
        (
            "pkg:rpm/redhat/openssl@1.1.1k-9?arch=x86_64&distro=redhat-8.9",
            ("redhat", "8"),
        ),
        # rpm namespace aliases resolve to the name Trivy matches on.
        ("pkg:rpm/rhel/bash@4.4-20.el8", ("redhat", "8")),
        ("pkg:rpm/amzn/curl@7.79-1.amzn2?distro=amazon-2", ("amazon", "2")),
        # apk keeps its full release id — that is alpine's advisory key.
        ("pkg:apk/alpine/musl@1.2.4-r0?distro=alpine-3.17.10", ("alpine", "3.17.10")),
        # deb: debian is keyed by major, ubuntu by major.minor.
        ("pkg:deb/debian/curl@7.74.0-1?distro=debian-11", ("debian", "11")),
        ("pkg:deb/debian/curl@7.74.0-1?distro=debian-11.7", ("debian", "11")),
        ("pkg:deb/ubuntu/curl@7.58.0-2?distro=ubuntu-18.04", ("ubuntu", "18.04")),
    ],
)
def test_classify_purl_reads_the_distro_and_version(
    purl: str, expected: tuple[str, str]
) -> None:
    assert classify_purl(purl) == expected


@pytest.mark.parametrize(
    "purl",
    [
        # Not a distro package at all.
        "pkg:npm/lodash@4.17.21",
        "pkg:maven/org.apache/commons@1.0",
        # A distro we carry no mapping for — never guessed at.
        "pkg:rpm/openwrt/busybox@1.35.0",
        "pkg:apk/wolfi/musl@1.2.4-r0?distro=wolfi-20230201",
        # rpm with no .elN suffix and no qualifier: the version is unknowable.
        "pkg:rpm/centos/openssl@1.0.2k-19",
        # deb / apk without a distro= qualifier: likewise, and these formats
        # state the release nowhere else.
        "pkg:deb/debian/curl@7.74.0-1",
        "pkg:apk/alpine/musl@1.2.4-r0",
        # Junk.
        "",
        "not-a-purl",
    ],
)
def test_classify_purl_declines_what_it_cannot_place(purl: str) -> None:
    assert classify_purl(purl) is None


def test_infer_os_context_takes_the_majority() -> None:
    context = infer_os_context(
        [
            "pkg:rpm/centos/a@1-1.el7",
            "pkg:rpm/centos/b@1-1.el7",
            "pkg:rpm/centos/c@1-1.el7",
            "pkg:rpm/rocky/d@1-1.el8",
            "pkg:npm/lodash@4.17.21",
        ]
    )
    assert context == OsContext(name="centos", version="7", votes=3, total=4)


def test_infer_os_context_returns_none_without_a_single_distro_package() -> None:
    assert infer_os_context(["pkg:npm/lodash@4.17.21", "pkg:pypi/flask@3.0.0"]) is None


# ---------------------------------------------------------------------------
# CycloneDX
# ---------------------------------------------------------------------------


def test_cyclonedx_gains_the_component_trivy_needs() -> None:
    result = enrich_sbom_bytes(
        _cdx(
            "pkg:rpm/centos/openssl@1.0.2k-19.el7",
            "pkg:rpm/centos/glibc@2.17-317.el7",
        )
    )
    assert result is not None
    assert result.synthesized == OsContext(
        name="centos", version="7", votes=2, total=2
    )

    added = _os_components(result.document)
    assert len(added) == 1
    # The literal shape Trivy reads. See the module docstring.
    assert added[0]["type"] == "operating-system"
    assert added[0]["name"] == "centos"
    assert added[0]["version"] == "7"
    # Traceable to us rather than to the supplier.
    assert added[0]["bom-ref"] == "trusca-os-context"


def test_cyclonedx_with_an_os_component_is_left_alone() -> None:
    """Including one whose version carries a minor.

    Upstream rewrote "rocky 8.10" to "8"; measured against Trivy 0.71.2 that
    changes no match, so a supplier's stated version is not edited.
    """
    document = _cdx(
        "pkg:rpm/rocky/openssl@1.1.1k-12.el8_9",
        extra=[{"type": "operating-system", "name": "rocky", "version": "8.10"}],
    )
    assert enrich_sbom_bytes(document) is None


def test_cyclonedx_counts_nested_components() -> None:
    """A document that describes its packages one level down still matches.

    Trivy reads nested components, so they have to vote — otherwise a
    container SBOM that groups packages under their image is read as having
    no distro packages at all.
    """
    document = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [
                {
                    "type": "container",
                    "name": "image",
                    "components": [
                        {
                            "type": "library",
                            "name": "openssl",
                            "purl": "pkg:rpm/centos/openssl@1.0.2k-19.el7",
                        },
                        {
                            "type": "library",
                            "name": "glibc",
                            "purl": "pkg:rpm/centos/glibc@2.17-317.el7",
                        },
                    ],
                }
            ],
        }
    ).encode()

    result = enrich_sbom_bytes(document)
    assert result is not None
    assert result.synthesized.name == "centos"


def test_cyclonedx_without_distro_packages_is_unchanged() -> None:
    assert enrich_sbom_bytes(_cdx("pkg:npm/lodash@4.17.21")) is None


# ---------------------------------------------------------------------------
# SPDX
# ---------------------------------------------------------------------------


def test_spdx_gains_a_package_with_the_id_prefix_trivy_matches_on() -> None:
    result = enrich_sbom_bytes(
        _spdx(
            "pkg:rpm/centos/openssl@1.0.2k-19.el7",
            "pkg:rpm/centos/glibc@2.17-317.el7",
        )
    )
    assert result is not None
    assert result.synthesized.name == "centos"

    added = _os_packages(result.document)
    assert len(added) == 1
    # The contract measured against Trivy: the SPDXID prefix is what makes this
    # a readable operating system. The purpose is correct SPDX but not what
    # Trivy keys on.
    assert added[0]["SPDXID"].startswith("SPDXRef-OperatingSystem")
    assert added[0]["SPDXID"] == "SPDXRef-OperatingSystem-trusca-os-context"
    assert added[0]["primaryPackagePurpose"] == "OPERATING_SYSTEM"
    assert added[0]["name"] == "centos"
    assert added[0]["versionInfo"] == "7"


def test_spdx_with_a_readable_os_package_is_left_alone() -> None:
    document = _spdx(
        "pkg:rpm/centos/openssl@1.0.2k-19.el7",
        extra=[
            {
                "SPDXID": "SPDXRef-OperatingSystem",
                "name": "centos",
                "versionInfo": "7",
            }
        ],
    )
    assert enrich_sbom_bytes(document) is None


def test_spdx_purpose_without_the_id_prefix_still_gets_a_package() -> None:
    """The gap this closes is Trivy's, not SPDX's.

    A package marked ``primaryPackagePurpose: OPERATING_SYSTEM`` under some
    other SPDXID is a correct SPDX statement that Trivy does not read — so the
    document matches nothing, and one it does read is added.
    """
    document = _spdx(
        "pkg:rpm/centos/openssl@1.0.2k-19.el7",
        extra=[
            {
                "SPDXID": "SPDXRef-Distro",
                "name": "centos",
                "versionInfo": "7",
                "primaryPackagePurpose": "OPERATING_SYSTEM",
            }
        ],
    )
    result = enrich_sbom_bytes(document)
    assert result is not None
    assert len(_os_packages(result.document)) == 1


# ---------------------------------------------------------------------------
# Idempotency and refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("build", [_cdx, _spdx])
def test_enriching_twice_adds_one_component(build: Any) -> None:
    """A Celery ``acks_late`` re-entry re-runs the stage on the same input."""
    first = enrich_sbom_bytes(build("pkg:rpm/centos/openssl@1.0.2k-19.el7"))
    assert first is not None
    assert enrich_sbom_bytes(first.document) is None


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json at all",
        b"[]",
        b'{"bomFormat": "CycloneDX", "specVersion": "1.5"}',  # no components
        b'{"spdxVersion": "SPDX-2.3"}',  # no packages
        b"SPDXVersion: SPDX-2.3\nPackageName: openssl\n",  # Tag-Value: not rewritten
    ],
)
def test_documents_that_are_returned_untouched(raw: bytes) -> None:
    assert enrich_sbom_bytes(raw) is None


def test_malformed_entries_do_not_stop_the_inference() -> None:
    """Uploaded documents are attacker-shaped; a bad entry is skipped, not fatal."""
    document = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [
                "a string where an object belongs",
                {"type": "library", "purl": None},
                {"type": "library", "purl": 12345},
                {"type": "library", "purl": "pkg:rpm/centos/openssl@1.0.2k-19.el7"},
            ],
        }
    ).encode()

    result = enrich_sbom_bytes(document)
    assert result is not None
    assert result.synthesized.name == "centos"
