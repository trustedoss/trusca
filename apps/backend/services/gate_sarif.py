# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Render a scan's open vulnerability findings as SARIF 2.1.0 (ER29).

Why this exists
---------------
A self-hosting organisation that runs CI on GitHub reads security results in
the repository's Code scanning tab, because that is where every other scanner
puts them and where branch protection can require them. Without a SARIF export
our results are reachable only by opening the portal, so a reviewer looking at
a pull request does not see them at all. That is friction exactly where a new
adopter forms an opinion.

SARIF is also not GitHub-specific: GitLab reads it for its security dashboard,
and several review tools ingest it. Emitting the standard document is what
makes the result portable, so this lives in the backend rather than being
assembled with `jq` inside one CI action.

Shape decisions
---------------
The document follows the conventions an established scanner already uses for
dependency findings, checked against Trivy's own SARIF output rather than read
off the specification, because what matters in practice is what GitHub's
ingester accepts:

* One rule per distinct CVE in ``tool.driver.rules``, with results referring to
  it by ``ruleId`` and ``ruleIndex``. A CVE affecting three components is one
  rule and three results.
* Every result carries a location. Dependency findings have no source file to
  point at (this product records a component graph, not manifest line numbers),
  so the location names the scanned project with ``uriBaseId: "ROOTPATH"`` and
  a degenerate 1,1 region, which is what Trivy emits for the same situation.
  The human-readable part goes in the location's own message, where GitHub
  shows it: ``project: component@version``.
* ``level`` maps severity onto SARIF's three levels. SARIF has no "critical",
  so critical and high both become ``error``; that is deliberate, since the
  distinction survives in the message and in the rule's tags, and collapsing it
  into ``warning`` would hide a critical finding in the same bucket as a
  medium.

Suppressed findings are excluded here even though the build gate counts them.
The gate is asking "may this build proceed", where a suppression is a local
decision that does not change the code's exposure; code scanning is asking
"what should a reviewer look at", where re-raising something a team already
triaged is how a scanner trains people to ignore it. The two answers differ on
purpose, and `_CLOSED_FINDING_STATUSES` is the shared vocabulary for it.
"""

from __future__ import annotations

from typing import Any, Final

#: The published SARIF 2.1.0 schema. GitHub validates uploads against it.
SARIF_SCHEMA: Final = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json"
)
SARIF_VERSION: Final = "2.1.0"

TOOL_NAME: Final = "TRUSCA"
TOOL_INFORMATION_URI: Final = "https://github.com/trustedoss/trusca"

#: SARIF defines exactly these result levels. "critical" is not one of them.
_LEVEL_ERROR: Final = "error"
_LEVEL_WARNING: Final = "warning"
_LEVEL_NOTE: Final = "note"

_SEVERITY_TO_LEVEL: Final[dict[str, str]] = {
    "critical": _LEVEL_ERROR,
    "high": _LEVEL_ERROR,
    "medium": _LEVEL_WARNING,
    "low": _LEVEL_NOTE,
    "unknown": _LEVEL_NOTE,
}


def severity_to_level(severity: str | None) -> str:
    """Map a finding severity onto a SARIF level.

    An unrecognised severity becomes ``note`` rather than raising: a document
    that reports a finding at the wrong level is far better than a CI step that
    fails because a new severity string appeared.
    """
    if not severity:
        return _LEVEL_NOTE
    return _SEVERITY_TO_LEVEL.get(severity.strip().lower(), _LEVEL_NOTE)


def _rule_for(
    cve_id: str,
    severity: str,
    title: str | None,
    description: str | None,
) -> dict[str, Any]:
    # shortDescription is what GitHub shows in the alert list, so it must stay
    # one line; fullDescription carries the prose.
    short = (title or f"{cve_id} affects a dependency of this project").strip()
    rule: dict[str, Any] = {
        "id": cve_id,
        "name": "VulnerableDependency",
        "shortDescription": {"text": short.splitlines()[0][:1000]},
        "defaultConfiguration": {"level": severity_to_level(severity)},
        "properties": {
            "security-severity": _security_severity(severity),
            "tags": ["security", "vulnerability", f"severity:{(severity or 'unknown').lower()}"],
        },
    }
    if description:
        rule["fullDescription"] = {"text": description.strip()[:3000]}
    if cve_id.upper().startswith("CVE-"):
        rule["helpUri"] = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
    return rule


def _security_severity(severity: str | None) -> str:
    """GitHub sorts and filters code-scanning alerts on this numeric string.

    Without it every alert lands in the same bucket regardless of severity, so
    the ordering a reviewer relies on is lost. The numbers are the midpoints of
    the CVSS v3 bands GitHub documents for this field.
    """
    return {
        "critical": "9.5",
        "high": "7.5",
        "medium": "5.0",
        "low": "2.0",
    }.get((severity or "").strip().lower(), "0.0")


def build_sarif(
    *,
    findings: list[dict[str, Any]],
    project_name: str,
    tool_version: str,
) -> dict[str, Any]:
    """Build the SARIF document for one scan's open findings.

    ``findings`` items carry ``cve_id``, ``severity``, ``component_name``,
    ``component_version`` and optionally ``fixed_version``, ``title`` and
    ``description``. A scan with no findings still produces a valid document
    with an empty ``results`` list, which is the correct thing to upload: it is
    what clears previously-reported alerts on the branch. Returning nothing, or
    skipping the upload, would leave fixed alerts showing forever.
    """
    rules: list[dict[str, Any]] = []
    rule_index: dict[str, int] = {}
    results: list[dict[str, Any]] = []

    for finding in findings:
        cve_id = str(finding.get("cve_id") or "").strip()
        if not cve_id:
            # A finding with no identifier cannot be a rule, and inventing one
            # would collide across scans.
            continue
        severity = str(finding.get("severity") or "unknown")
        component = str(finding.get("component_name") or "unknown")
        version = str(finding.get("component_version") or "unknown")
        fixed = finding.get("fixed_version")

        if cve_id not in rule_index:
            rule_index[cve_id] = len(rules)
            rules.append(
                _rule_for(cve_id, severity, finding.get("title"), finding.get("description"))
            )

        message_lines = [
            f"Package: {component}",
            f"Installed Version: {version}",
            f"Vulnerability {cve_id}",
            f"Severity: {severity.upper()}",
        ]
        # Whether a fix exists is the first thing a reviewer needs, so it is in
        # the message rather than only in a property nothing renders.
        message_lines.append(
            f"Fixed Version: {fixed}" if fixed else "Fixed Version: none available"
        )

        results.append(
            {
                "ruleId": cve_id,
                "ruleIndex": rule_index[cve_id],
                "level": severity_to_level(severity),
                "message": {"text": "\n".join(message_lines)},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": project_name,
                                "uriBaseId": "ROOTPATH",
                            },
                            "region": {
                                "startLine": 1,
                                "startColumn": 1,
                                "endLine": 1,
                                "endColumn": 1,
                            },
                        },
                        "message": {"text": f"{project_name}: {component}@{version}"},
                    }
                ],
                # Keeps GitHub from re-raising the same alert as new when the
                # scan is re-run; without it a reviewer sees churn on every run.
                "partialFingerprints": {
                    "truscaFindingId": f"{cve_id}:{component}:{version}",
                },
            }
        )

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "informationUri": TOOL_INFORMATION_URI,
                        "version": tool_version,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


__all__ = [
    "SARIF_SCHEMA",
    "SARIF_VERSION",
    "build_sarif",
    "severity_to_level",
]
