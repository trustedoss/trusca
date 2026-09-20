"""
License names as real Maven POMs spell them, mapped through ``normalize_spdx_id``.

Every name here is read out of an unmodified POM under ``tests/fixtures/maven_poms``
(see the PROVENANCE there), not typed in by hand. The recorded name is asserted
against the file first, so a fixture that stops carrying the name fails loudly
instead of leaving the mapping assertion checking a string of our own making.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.license_fetcher.base import normalize_spdx_id
from integrations.license_fetcher.maven import _parse_license_xml

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "maven_poms"

# (POM file, name the POM declares, SPDX id we map it to or None for "left unknown")
REAL_NAMES = [
    ("org.hamcrest_hamcrest_2.2.pom", "BSD License 3", "BSD-3-Clause"),
    ("org.eclipse.angus_angus-activation-project_2.0.0.pom", "EDL 1.0", "BSD-3-Clause"),
    (
        "com.sun.xml.bind.mvn_jaxb-parent_4.0.2.pom",
        "Eclipse Distribution License - v 1.0",
        "BSD-3-Clause",
    ),
    (
        "jakarta.persistence_jakarta.persistence-api_3.1.0.pom",
        "Eclipse Public License v. 2.0",
        "EPL-2.0",
    ),
    ("org.junit.jupiter_junit-jupiter_5.10.1.pom", "Eclipse Public License v2.0", "EPL-2.0"),
    ("org.opentest4j_opentest4j_1.3.0.pom", "The Apache License, Version 2.0", "Apache-2.0"),
    # The name alone does not say 2-clause or 3-clause, so it stays unknown.
    ("org.antlr_antlr4-master_4.10.1.pom", "The BSD License", None),
]


@pytest.mark.parametrize("pom,name,expected", REAL_NAMES, ids=[r[1] for r in REAL_NAMES])
def test_real_pom_license_name_maps_as_recorded(pom: str, name: str, expected: str | None) -> None:
    declared = _parse_license_xml((FIXTURES / pom).read_text(encoding="utf-8"))
    assert declared is not None
    assert declared[0] == name
    assert normalize_spdx_id(name) == expected


@pytest.mark.parametrize(
    "raw",
    ["The BSD License", "the bsd license", "BSD License 2", "Eclipse Distribution License"],
)
def test_ambiguous_bsd_and_edl_names_stay_unknown(raw: str) -> None:
    assert normalize_spdx_id(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("GNU General Public License, version 2", "GPL-2.0-only"),
        ("GNU General Public License v3 or later", "GPL-3.0-or-later"),
        ("GNU Lesser General Public License, version 2.1", "LGPL-2.1-only"),
        ("GNU Affero General Public License v3 or later", "AGPL-3.0-or-later"),
        ("GPL-2.0 WITH Classpath-exception-2.0", "GPL-2.0-only"),
        ("Eclipse Public License - v 2.0", "EPL-2.0"),
    ],
)
def test_neighbouring_copyleft_and_eclipse_names_are_unchanged(raw: str, expected: str) -> None:
    assert normalize_spdx_id(raw) == expected
