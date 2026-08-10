# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for ``tasks.scan_container._component_identity``.

The container persist path used to name every package in every image
``pkg:apk/{name}@{version}`` with package type ``"apk"``. It lost no findings,
so the failure was invisible: a Rocky image's rpms, a Debian image's debs and a
pip package inside a python image were all inventoried as alpine packages.

These pin the identity derivation itself — the DB-level behaviour is covered by
``tests/integration/scan/test_container_multi_cve.py`` against recorded
``trivy image`` output.
"""

from __future__ import annotations

from typing import Any

import pytest

from tasks.scan_container import _component_identity


def _vuln(purl: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "PkgName": "openssl",
        "InstalledVersion": "3.0.7-24.el9",
        "VulnerabilityID": "CVE-2026-0001",
    }
    if purl is not None:
        entry["PkgIdentifier"] = {"PURL": purl, "UID": "abcdef0123456789"}
    return entry


@pytest.mark.parametrize(
    ("purl", "expected"),
    [
        (
            "pkg:rpm/rocky/openssl@3.0.7-24.el9?arch=x86_64&distro=rocky-9.3",
            (
                "pkg:rpm/rocky/openssl@3.0.7-24.el9",
                "pkg:rpm/rocky/openssl",
                "rpm",
            ),
        ),
        (
            "pkg:deb/debian/apt@3.0.3?arch=amd64&distro=debian-13.6",
            ("pkg:deb/debian/apt@3.0.3", "pkg:deb/debian/apt", "deb"),
        ),
        (
            "pkg:apk/alpine/busybox@1.36.1-r20?arch=x86_64&distro=3.19.9",
            ("pkg:apk/alpine/busybox@1.36.1-r20", "pkg:apk/alpine/busybox", "apk"),
        ),
        # A language package found inside an image keeps its own ecosystem.
        ("pkg:pypi/pip@25.0.1", ("pkg:pypi/pip@25.0.1", "pkg:pypi/pip", "pypi")),
        # A scoped npm name survives the version split: the encoded ``%40`` is
        # not the ``@`` that separates the version.
        (
            "pkg:npm/%40types/node@20.0.0",
            ("pkg:npm/%40types/node@20.0.0", "pkg:npm/%40types/node", "npm"),
        ),
    ],
)
def test_identity_comes_from_the_purl_trivy_attached(
    purl: str, expected: tuple[str, str, str]
) -> None:
    assert (
        _component_identity(
            _vuln(purl),
            ecosystem="rocky",
            pkg_name="openssl",
            installed="3.0.7-24.el9",
        )
        == expected
    )


def test_a_mappable_type_still_works_without_an_attached_purl() -> None:
    """Older reports, or a Result Trivy did not identify per-package."""
    assert _component_identity(
        _vuln(None),
        ecosystem="npm",
        pkg_name="lodash",
        installed="4.17.21",
    ) == ("pkg:npm/lodash@4.17.21", "pkg:npm/lodash", "npm")


@pytest.mark.parametrize(
    ("purl", "ecosystem"),
    [
        # Neither an attached PURL nor a Type any reconstruction maps.
        (None, "an-ecosystem-nobody-maps"),
        (None, None),
        # Attached values that are not usable identities.
        ("not-a-purl", "an-ecosystem-nobody-maps"),
        ("https://example.test/pkg", "an-ecosystem-nobody-maps"),
        # Version-less: cannot name a component VERSION.
        ("pkg:rpm/rocky/openssl", "an-ecosystem-nobody-maps"),
        # Control characters never reach a stored identity.
        ("pkg:rpm/rocky/evil\r\nssl@1.0", "an-ecosystem-nobody-maps"),
    ],
)
def test_declines_rather_than_guessing_a_type(
    purl: str | None, ecosystem: str | None
) -> None:
    assert (
        _component_identity(
            _vuln(purl),
            ecosystem=ecosystem,
            pkg_name="mystery",
            installed="1.0",
        )
        is None
    )
