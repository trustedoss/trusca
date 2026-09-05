# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Which feed reported a finding, derived from the stored Trivy blob (E5a).

Every case here is taken from a recorded report in
``tests/fixtures/trivy/`` rather than composed, because the shapes that matter
are the ones the scanner actually emits. Its PROVENANCE.md says which report
each came from.

The absent case is the important one. An SBOM scan of CentOS 7 RPMs reports no
``DataSource`` on any of its twelve findings, so "no source" is a state the
product meets in practice and not a defensive branch.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from services.vulnerability_service import matching_provenance

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "trivy"


def _findings(name: str) -> list[dict]:
    report = json.loads((FIXTURES / name).read_text())
    return [
        v for r in (report.get("Results") or []) for v in (r.get("Vulnerabilities") or [])
    ]


@pytest.mark.parametrize(
    ("fixture", "expected_id", "expected_name"),
    [
        ("npm-lockfile-source-report.json", "ghsa", "GitHub Security Advisory npm"),
        ("pip-requirements-source-report.json", "ghsa", "GitHub Security Advisory pip"),
        ("alpine-3.19-image-report.json", "alpine", "Alpine Secdb"),
        ("rocky-9-image-report.json", "rocky", "Rocky Linux updateinfo"),
        ("debian-python-image-report.json", "debian", "Debian Security Tracker"),
    ],
)
def test_the_feed_is_named_for_each_recorded_ecosystem(
    fixture: str, expected_id: str, expected_name: str
) -> None:
    """Source scans and container scans both, because both are product paths.

    Listing five ecosystems rather than one is what caught the assumption this
    started from: the synthetic source-path fixture has no DataSource, and
    reading only that says the field never arrives on a source scan. It arrives
    on all 34 npm findings and all 14 pip ones.
    """
    first = _findings(fixture)[0]
    out = matching_provenance(first)

    assert out is not None, f"{fixture} reports a source and none was derived"
    assert out["id"] == expected_id
    assert out["name"] == expected_name
    assert out["feed_url"].startswith("http"), out


def test_a_scan_that_names_no_source_yields_none() -> None:
    """The recorded absence, asserted against every finding in that report.

    Asserting on one would leave open that the others differ, and what the
    drawer says depends on this being uniform rather than occasional.
    """
    findings = _findings("centos7-rpm-sbom-report.json")
    assert findings, "the fixture no longer holds any findings"
    assert all(matching_provenance(f) is None for f in findings), (
        "some CentOS findings now derive a source; the drawer's wording says "
        "the scanner reported none, which would then be wrong for those"
    )


def test_every_npm_finding_derives_one() -> None:
    """The other side of the same question, over the whole report.

    The five-ecosystem test above reads the first finding of each. This says
    the field is not occasional in the ecosystem the product scans most.
    """
    findings = _findings("npm-lockfile-source-report.json")
    derived = [matching_provenance(f) for f in findings]
    assert len(findings) == 34, len(findings)
    assert all(d is not None for d in derived), (
        f"{sum(d is None for d in derived)} of {len(findings)} npm findings "
        "derive no source"
    )


@pytest.mark.parametrize(
    "blob",
    [
        None,
        {},
        {"DataSource": None},
        {"DataSource": "ghsa"},
        {"DataSource": {}},
        {"DataSource": {"ID": "ghsa"}},
        {"DataSource": {"Name": "   "}},
        "not a dict at all",
        {"_truncated": True, "_preview": "..."},
    ],
)
def test_a_blob_that_carries_no_usable_source_yields_none(blob: object) -> None:
    """Shapes the column can hold that are not a usable source.

    The last is what the JSONB size guard writes when a finding's blob exceeds
    the row limit. No recorded finding comes close to it (the largest observed
    is 17,812 bytes against a 256 KiB ceiling), but the column can hold it and
    reading ``DataSource`` off it must not raise.

    ``{"ID": "ghsa"}`` with no name yields nothing on purpose: an identifier is
    not a label, and inventing one would put a word on screen that no feed
    calls itself.
    """
    assert matching_provenance(blob) is None


def test_a_source_with_no_url_still_names_the_feed() -> None:
    """The url is the optional part, not the name.

    A feed whose name is known is worth showing without a link; a link with no
    name is not.
    """
    out = matching_provenance({"DataSource": {"ID": "x", "Name": "Some Feed"}})
    assert out == {"id": "x", "name": "Some Feed"}
