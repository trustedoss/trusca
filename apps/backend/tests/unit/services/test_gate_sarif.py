# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""SARIF mapping (ER29).

The document is consumed by GitHub code scanning, which rejects a malformed
upload wholesale rather than skipping the bad result, so the shape assertions
here are the contract. The expected shapes were taken from what an established
scanner actually emits for dependency findings, not from prose in the spec.
"""

from __future__ import annotations

import pytest

from services.gate_sarif import SARIF_VERSION, build_sarif, severity_to_level


def _finding(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "cve_id": "CVE-2024-0001",
        "severity": "critical",
        "component_name": "left-pad",
        "component_version": "1.0.0",
        "fixed_version": "1.0.1",
        "title": "A summary line",
        "description": "Longer prose about the vulnerability.",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        # SARIF has no "critical" level, so critical and high share `error`.
        # Collapsing either into `warning` would file it beside the mediums.
        ("critical", "error"),
        ("high", "error"),
        ("medium", "warning"),
        ("low", "note"),
        ("unknown", "note"),
        ("CRITICAL", "error"),
        # A severity string nobody has seen must not raise: a wrong level is a
        # bad row, an exception is a failed CI step.
        ("moderate-ish", "note"),
        (None, "note"),
        ("", "note"),
    ],
)
def test_severity_maps_onto_a_sarif_level(severity: str | None, expected: str) -> None:
    assert severity_to_level(severity) == expected


def test_empty_findings_still_produce_a_valid_run() -> None:
    """An empty run is what clears alerts that were fixed."""
    doc = build_sarif(findings=[], project_name="demo", tool_version="1.2.3")
    assert doc["version"] == SARIF_VERSION
    run = doc["runs"][0]
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []
    assert run["tool"]["driver"]["version"] == "1.2.3"


def test_one_cve_on_two_components_is_one_rule_and_two_results() -> None:
    """Rules are per-CVE; results are per-occurrence.

    Emitting two rules with the same id makes GitHub deduplicate them and the
    ruleIndex references then point at the wrong entry.
    """
    doc = build_sarif(
        findings=[
            _finding(component_name="a"),
            _finding(component_name="b"),
        ],
        project_name="demo",
        tool_version="1",
    )
    run = doc["runs"][0]
    assert len(run["tool"]["driver"]["rules"]) == 1
    assert len(run["results"]) == 2
    for result in run["results"]:
        assert run["tool"]["driver"]["rules"][result["ruleIndex"]]["id"] == result["ruleId"]


def test_rule_index_addresses_the_right_rule_across_several_cves() -> None:
    doc = build_sarif(
        findings=[
            _finding(cve_id="CVE-2024-0001", severity="critical"),
            _finding(cve_id="CVE-2024-0002", severity="low"),
            _finding(cve_id="CVE-2024-0003", severity="medium"),
            _finding(cve_id="CVE-2024-0002", severity="low", component_name="other"),
        ],
        project_name="demo",
        tool_version="1",
    )
    run = doc["runs"][0]
    assert len(run["tool"]["driver"]["rules"]) == 3
    for result in run["results"]:
        assert run["tool"]["driver"]["rules"][result["ruleIndex"]]["id"] == result["ruleId"]


def test_every_result_carries_a_location() -> None:
    """GitHub rejects a result with no location."""
    doc = build_sarif(findings=[_finding()], project_name="my-project", tool_version="1")
    location = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "my-project"
    assert location["artifactLocation"]["uriBaseId"] == "ROOTPATH"
    assert location["region"]["startLine"] == 1


def test_a_finding_with_no_identifier_is_skipped() -> None:
    """It cannot become a rule, and a synthetic id would collide across scans."""
    doc = build_sarif(
        findings=[_finding(cve_id=""), _finding(cve_id=None), _finding()],
        project_name="demo",
        tool_version="1",
    )
    assert len(doc["runs"][0]["results"]) == 1


def test_the_message_says_whether_a_fix_exists() -> None:
    """First thing a reviewer needs, so it is in the message, not a property."""
    with_fix = build_sarif(
        findings=[_finding(fixed_version="2.0.0")], project_name="d", tool_version="1"
    )
    assert "Fixed Version: 2.0.0" in with_fix["runs"][0]["results"][0]["message"]["text"]

    without = build_sarif(
        findings=[_finding(fixed_version=None)], project_name="d", tool_version="1"
    )
    assert "none available" in without["runs"][0]["results"][0]["message"]["text"]


def test_security_severity_lets_github_sort_alerts() -> None:
    """Without it every alert lands in one bucket and the ordering is lost."""
    doc = build_sarif(
        findings=[
            _finding(cve_id="CVE-1", severity="critical"),
            _finding(cve_id="CVE-2", severity="low"),
        ],
        project_name="d",
        tool_version="1",
    )
    by_id = {r["id"]: r for r in doc["runs"][0]["tool"]["driver"]["rules"]}
    assert by_id["CVE-1"]["properties"]["security-severity"] == "9.5"
    assert by_id["CVE-2"]["properties"]["security-severity"] == "2.0"


def test_fingerprints_are_stable_across_runs() -> None:
    """Otherwise every re-scan re-raises the same alert as new."""
    args = {"findings": [_finding()], "project_name": "d", "tool_version": "1"}
    first = build_sarif(**args)  # type: ignore[arg-type]
    second = build_sarif(**args)  # type: ignore[arg-type]
    assert (
        first["runs"][0]["results"][0]["partialFingerprints"]
        == second["runs"][0]["results"][0]["partialFingerprints"]
    )


def test_a_short_description_stays_one_line() -> None:
    """It is the alert-list title; a newline there breaks the rendering."""
    doc = build_sarif(
        findings=[_finding(title="first line\nsecond line")],
        project_name="d",
        tool_version="1",
    )
    short = doc["runs"][0]["tool"]["driver"]["rules"][0]["shortDescription"]["text"]
    assert "\n" not in short
    assert short == "first line"


# ---------------------------------------------------------------------------
# Schema conformance
#
# Checked against the required-property declarations copied from the published
# SARIF 2.1.0 schema rather than by pulling the schema at test time: the suite
# must not depend on the network, and adding a JSON-schema validator as a
# runtime dependency to assert a fixed shape is not worth it. The values below
# were read out of the schema the `$schema` field names.
# ---------------------------------------------------------------------------

_SARIF_REQUIRED: dict[str, tuple[str, ...]] = {
    "sarifLog": ("runs", "version"),
    "run": ("tool",),
    "tool": ("driver",),
    "toolComponent": ("name",),
    "reportingDescriptor": ("id",),
    "result": ("message",),
}

#: The only values SARIF 2.1.0 allows for `result.level`.
_SARIF_LEVELS = ("none", "note", "warning", "error")


def test_document_carries_every_schema_required_property() -> None:
    doc = build_sarif(findings=[_finding()], project_name="demo", tool_version="1")
    run = doc["runs"][0]

    for key in _SARIF_REQUIRED["sarifLog"]:
        assert key in doc, f"sarifLog is missing {key}"
    for key in _SARIF_REQUIRED["run"]:
        assert key in run
    for key in _SARIF_REQUIRED["tool"]:
        assert key in run["tool"]
    for key in _SARIF_REQUIRED["toolComponent"]:
        assert key in run["tool"]["driver"]
    for key in _SARIF_REQUIRED["reportingDescriptor"]:
        assert key in run["tool"]["driver"]["rules"][0]
    for key in _SARIF_REQUIRED["result"]:
        assert key in run["results"][0]


@pytest.mark.parametrize("severity", ["critical", "high", "medium", "low", "unknown", "weird"])
def test_every_emitted_level_is_one_the_schema_allows(severity: str) -> None:
    """An out-of-enum level makes GitHub reject the whole upload, not one row."""
    doc = build_sarif(
        findings=[_finding(severity=severity)], project_name="d", tool_version="1"
    )
    assert doc["runs"][0]["results"][0]["level"] in _SARIF_LEVELS
    rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["defaultConfiguration"]["level"] in _SARIF_LEVELS


def test_version_is_the_one_the_schema_pins() -> None:
    doc = build_sarif(findings=[], project_name="d", tool_version="1")
    assert doc["version"] == "2.1.0"
