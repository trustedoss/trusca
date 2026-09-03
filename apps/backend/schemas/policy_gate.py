# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Policy gate response schemas — Phase 5 PR #17.

The policy gate is the build-blocking decision the CI pipeline asks the
portal to make: based on the project's most recent successful scan, should
this build pass or fail?

Two HTTP shapes live here:

- :class:`GateResultResponse` — body of
  ``GET /v1/projects/{project_id}/gate-result``. Mirrors
  :class:`services.policy_gate.GateResult` but carries datetimes in ISO-8601
  rather than Python ``datetime`` objects so OpenAPI documents the wire
  format precisely.

- :class:`PostPRCommentRequest` / :class:`PostPRCommentResponse` — request +
  response for ``POST /v1/scans/{scan_id}/post-pr-comment``. The endpoint
  posts (or updates) a Markdown comment on a GitHub PR; the request carries
  the SCM context the CI runner already knows.

Closed-enum mirrors are kept in lock-step with
:data:`services.policy_gate.GateOutcome`.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GateOutcome = Literal["pass", "fail"]


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/gate-result
# ---------------------------------------------------------------------------


class GateResultResponse(BaseModel):
    """Build-gate verdict for the project's latest successful scan."""

    model_config = ConfigDict(from_attributes=True)

    gate: GateOutcome = Field(
        description="Overall outcome. ``pass`` when no critical CVEs and no "
        "forbidden licenses are present, otherwise ``fail``.",
    )
    reason: str | None = Field(
        default=None,
        description="Human-readable explanation when ``gate == 'fail'``. ``null`` "
        "for passing builds.",
    )
    critical_cve_count: int = Field(
        ge=0,
        description="Number of open critical-severity findings on the evaluated "
        "scan. Open = status not in (not_affected, fixed, false_positive).",
    )
    forbidden_license_count: int = Field(
        ge=0,
        description="Distinct component_versions on the evaluated scan that carry "
        "at least one forbidden-classification license.",
    )
    epss_gate_count: int = Field(
        default=0,
        ge=0,
        description="Number of open findings on the evaluated scan whose CVE has an "
        "EPSS score at or above ``epss_threshold``. Always 0 when the EPSS gate is "
        "disabled (``epss_threshold == null``).",
    )
    epss_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="The active EPSS gate threshold in [0, 1], read from the "
        "``GATE_EPSS_THRESHOLD`` environment variable at evaluation time. ``null`` "
        "when the EPSS gate is disabled (unset/unparseable env), in which case the "
        "gate behaves exactly as the critical-CVE + forbidden-license gate.",
    )
    malicious_component_count: int = Field(
        default=0,
        ge=0,
        description="Distinct components on the evaluated scan that the vendored "
        "OSV MAL- snapshot lists as known-malicious. These block regardless of "
        "severity: a malicious package was published to attack whoever installs "
        "it, so the response is removal plus rotating the credentials the build "
        "could reach, not an upgrade. Not a vulnerability count — these never "
        "appear in ``critical_cve_count`` or any severity total.",
    )
    malicious_scan_assessed: bool = Field(
        default=False,
        description=(
            "Whether this scan's components actually carry malicious verdicts. "
            "``false`` means the scan predates the feature, ran with flagging "
            "off, or hit a snapshot problem — so ``malicious_component_count`` "
            "of 0 says nothing was checked rather than nothing was found. "
            "``true`` on a scan with no components at all (there was nothing "
            "to check)."
        ),
    )
    component_outcome: str | None = Field(
        default=None,
        description=(
            "What the evaluated scan's SBOM contained. `components_found` is "
            "the ordinary case. `empty_no_manifests` and `empty_with_manifests` "
            "both mean the scan produced no components, so a `pass` here is the "
            "absence of anything to judge rather than a clean result, and a CI "
            "consumer must say so instead of printing an all-clear. `null` on a "
            "scan predating this capture: unknown, not either answer."
        ),
    )
    epss_outcome: str = Field(
        default="not_configured",
        description=(
            "What the EPSS axis was able to judge on the evaluated scan. "
            "`not_configured`: no threshold is set, so the axis is off by "
            "choice and `epss_gate_count` of 0 means what it says. "
            "`evaluated`: a threshold is set and every open finding carries a "
            "score, so the count is a complete answer. `partial`: some open "
            "findings carry no score, so a count of 0 does not mean nothing "
            "would have tripped the threshold. `no_data`: not one open "
            "finding carries a score, so the axis decided nothing at all and "
            "a `pass` here is the absence of a verdict rather than a clean "
            "one. A consumer must not print an all-clear for the EPSS axis on "
            "`no_data`, and should qualify it on `partial`."
        ),
    )
    epss_on_missing_data: str = Field(
        default="allow",
        description=(
            "What `GATE_EPSS_ON_MISSING_DATA` told the gate to do when the "
            "EPSS axis decided nothing. `allow` (the default) lets the build "
            "through, which is the historical behaviour; `block` fails it, so "
            "a configured threshold cannot be ignored in silence. `block` "
            "applies to `no_data` only and deliberately not to `partial`: "
            "gaps in EPSS coverage are normal, and an option that fires on a "
            "normal state is one nobody can leave switched on."
        ),
    )
    kev_gate_count: int = Field(
        default=0,
        description=(
            "Open findings whose CVE is listed in the CISA Known Exploited "
            "Vulnerabilities catalog. Always 0 when the KEV axis is off "
            "(`GATE_KEV_ENABLED` unset, the default). Read `kev_outcome` "
            "before treating a 0 as an all-clear."
        ),
    )
    kev_gate_enabled: bool = Field(
        default=False,
        description=(
            "Whether the KEV axis was switched on for this evaluation. When "
            "false, `kev_gate_count` is 0 because nothing was asked, not "
            "because nothing is exploited."
        ),
    )
    kev_outcome: str = Field(
        default="not_configured",
        description=(
            "What the KEV axis was able to judge. `not_configured`: the axis "
            "is off by choice. `evaluated`: every open finding has been "
            "through a KEV catalog sync. `partial`: some findings were "
            "discovered after the last successful sync, so their `kev` flag "
            "is the column default rather than an answer. `no_data`: the KEV "
            "catalog has never synced on this deployment, so every flag is a "
            "default and the axis decided nothing at all."
        ),
    )
    kev_on_missing_data: str = Field(
        default="allow",
        description=(
            "What `GATE_KEV_ON_MISSING_DATA` told the gate to do when the KEV "
            "axis decided nothing. `allow` (default) lets the build through; "
            "`block` fails it. As with EPSS, `block` applies to `no_data` "
            "only, never to `partial`."
        ),
    )
    eol_gate_count: int = Field(
        default=0,
        description=(
            "Components on the evaluated scan whose release line is past end "
            "of life. Always 0 when the EOL axis is off (`GATE_EOL_ENABLED` "
            "unset, the default)."
        ),
    )
    eol_gate_enabled: bool = Field(
        default=False,
        description="Whether the end-of-life axis was switched on for this evaluation.",
    )
    eol_outcome: str = Field(
        default="not_configured",
        description=(
            "What the end-of-life axis was able to judge. `not_configured`: "
            "off by choice. `evaluated`: every component on the scan has a "
            "lifecycle answer. `partial`: some components were never matched "
            "against the lifecycle catalog, which is the ordinary state, "
            "because the catalog covers a curated set of runtimes and "
            "frameworks rather than every dependency. `no_data`: not one "
            "component has an answer, so the axis decided nothing."
        ),
    )
    eol_on_missing_data: str = Field(
        default="allow",
        description=(
            "What `GATE_EOL_ON_MISSING_DATA` told the gate to do when the "
            "end-of-life axis decided nothing. Same semantics as the KEV and "
            "EPSS equivalents."
        ),
    )
    malicious_gate_enforced: bool = Field(
        default=True,
        description="Whether the known-malicious axis was active for this "
        "evaluation (``GATE_MALICIOUS_ENABLED``, on by default). When ``false`` "
        "the count is 0 because nothing was checked, NOT because nothing was "
        "found — consumers must not render that as a clean result.",
    )
    reachable_critical_cve_count: int = Field(
        default=0,
        ge=0,
        description="Subset of the open critical findings on the evaluated scan "
        "that an analyser has additionally proven REACHABLE (reachable IS TRUE) — "
        "a v2.3 priority signal. Always populated; ``0`` when no finding is proven "
        "reachable or no reachability analysis has run. By default this does NOT "
        "change the verdict (it is informational), unless the reachable-only "
        "critical mode is enabled (see ``reachable_gate_enforced``).",
    )
    reachable_gate_enforced: bool = Field(
        default=False,
        description="Whether the opt-in reachable-only critical mode "
        "(``GATE_REACHABLE_CRITICAL_ONLY`` env) was active for this evaluation. "
        "When ``false`` (default) every open critical counts — the legacy "
        "behaviour. When ``true`` the relaxation is requested, but it is "
        "applied SAFELY: it takes effect only on scans actually "
        "reachability-analysed (reachability is Go-only today), and even then it "
        "excludes ONLY criticals PROVEN unreachable — criticals not yet analysed "
        "(reachable IS NULL) keep blocking. On a scan with no reachability "
        "analysis the gate falls back to counting every open critical, so the "
        "flag never silently disables the gate for un-analysed ecosystems.",
    )
    project_id: uuid.UUID
    scan_id: uuid.UUID | None = Field(
        default=None,
        description="ID of the scan the verdict was computed against. ``null`` "
        "when the project has never had a successful scan, in which case "
        "``gate == 'pass'`` is returned by convention (no signal = no block).",
    )
    evaluated_at: datetime = Field(
        description="Server timestamp at which the verdict was computed (UTC, " "ISO-8601).",
    )


# ---------------------------------------------------------------------------
# POST /v1/scans/{scan_id}/post-pr-comment
# ---------------------------------------------------------------------------


# GitHub repository slugs are "owner/repo" with each segment composed of
# letters, digits, hyphen, underscore, dot. We pin a defensive pattern so
# attackers cannot inject path traversal or encoded URLs into the call we
# make to api.github.com.
_REPO_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


class PostPRCommentRequest(BaseModel):
    """CI-side input for ``POST /v1/scans/{scan_id}/post-pr-comment``."""

    repo_full_name: str = Field(
        min_length=3,
        max_length=140,
        description="GitHub ``owner/repo`` slug. Validated against the GitHub "
        "naming rules so we never call api.github.com with attacker-controlled "
        "path segments.",
    )
    pr_number: int = Field(
        ge=1,
        le=10_000_000,
        description="GitHub PR number.",
    )
    dry_run: bool = Field(
        default=False,
        description="When ``true`` the endpoint builds the Markdown comment but "
        "does not call GitHub. Useful for local CI rehearsals and used by the "
        "default integration tests so they do not require network access.",
    )

    @field_validator("repo_full_name")
    @classmethod
    def _validate_repo_full_name(cls, value: str) -> str:
        if not _REPO_SLUG_PATTERN.match(value):
            raise ValueError(
                "repo_full_name must look like 'owner/repo' with [A-Za-z0-9._-] segments",
            )
        return value


class PostPRCommentResponse(BaseModel):
    """Outcome of a PR-comment post."""

    model_config = ConfigDict(from_attributes=True)

    status: Literal["posted", "updated", "dry_run"]
    comment_id: int | None = Field(
        default=None,
        description="GitHub issue-comment id. ``null`` for ``dry_run`` and on "
        "transport errors that we choose not to surface to the caller.",
    )
    comment_url: str | None = Field(
        default=None,
        description="``html_url`` of the comment on github.com.",
    )
    body_preview: str = Field(
        description="The first 280 characters of the rendered comment body. The "
        "full body is never returned because it can grow large; the preview "
        "is enough for the CI runner to log a sanity check.",
    )
    gate: GateOutcome = Field(
        description="Echo of the gate verdict the comment reports. Lets the CI "
        "runner branch on the build-blocking decision in a single round-trip.",
    )


__all__ = [
    "GateOutcome",
    "GateResultResponse",
    "PostPRCommentRequest",
    "PostPRCommentResponse",
]
