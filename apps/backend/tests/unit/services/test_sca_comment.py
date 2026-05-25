"""
Unit tests for ``services/sca_comment.py`` — Phase 5 PR #17.

The Markdown builder and the ``post_pr_comment`` orchestrator are pure
async code with no database dependency. We exercise them with an
``httpx.MockTransport`` so the GitHub create-or-update logic gets full
coverage without going to the network.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from services.policy_gate import GateResult
from services.sca_comment import (
    COMMENT_MARKER,
    CommentSummary,
    RecommendedUpgrade,
    SCACommentBadGateway,
    SCACommentUnauthorized,
    build_pr_comment_markdown,
    post_pr_comment,
)


def _make_gate_result(
    *,
    gate: str = "fail",
    reason: str | None = "2 critical CVEs detected",
    critical_cve_count: int = 2,
    forbidden_license_count: int = 0,
    scan_id: uuid.UUID | None = None,
    reachable_critical_cve_count: int = 0,
    reachable_gate_enforced: bool = False,
    reachable_relaxation_applied: bool = False,
) -> GateResult:
    return GateResult(
        gate=gate,  # type: ignore[arg-type]
        reason=reason,
        critical_cve_count=critical_cve_count,
        forbidden_license_count=forbidden_license_count,
        project_id=uuid.uuid4(),
        scan_id=scan_id or uuid.uuid4(),
        evaluated_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
        reachable_critical_cve_count=reachable_critical_cve_count,
        reachable_gate_enforced=reachable_gate_enforced,
        reachable_relaxation_applied=reachable_relaxation_applied,
    )


def _make_summary(**overrides: Any) -> CommentSummary:
    base = {
        "components_count": 200,
        "severity_distribution": {
            "critical": 2,
            "high": 5,
            "medium": 12,
            "low": 7,
            "info": 0,
            "none": 174,
        },
        "license_distribution": {
            "forbidden": 1,
            "conditional": 8,
            "allowed": 145,
            "unknown": 0,
        },
        "project_url": "https://portal.example.com/projects/abc",
    }
    base.update(overrides)
    return CommentSummary(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------


def test_markdown_starts_with_marker_and_renders_fail_summary() -> None:
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(),
        summary=_make_summary(),
    )

    assert body.startswith(COMMENT_MARKER)
    assert "TrustedOSS SCA Report" in body
    assert "**FAIL**" in body
    assert "2 critical CVEs detected" in body
    assert "Critical**: 2" in body
    assert "Forbidden: 1" in body
    assert "https://portal.example.com/projects/abc" in body


def test_markdown_pass_omits_reason_line() -> None:
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(
            gate="pass", reason=None, critical_cve_count=0, forbidden_license_count=0
        ),
        summary=_make_summary(
            severity_distribution={
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "info": 0,
                "none": 200,
            },
            license_distribution={
                "forbidden": 0,
                "conditional": 0,
                "allowed": 200,
                "unknown": 0,
            },
        ),
    )

    assert "**PASS**" in body
    assert "Reason" not in body


def test_markdown_skips_view_link_when_project_url_missing() -> None:
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(),
        summary=_make_summary(project_url=None),
    )
    assert "View full report" not in body


# ---------------------------------------------------------------------------
# Reachable-only mode advisory (security-reviewer fix-first, Medium #2)
# ---------------------------------------------------------------------------


def test_markdown_omits_reachable_advisory_when_mode_off() -> None:
    """Mode off → no advisory line; legacy body byte-for-byte preserved."""
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(reachable_gate_enforced=False),
        summary=_make_summary(),
    )
    assert "Reachable-only critical mode" not in body


def test_markdown_shows_reachable_advisory_when_relaxation_applied() -> None:
    """Mode on AND it took effect → advisory states the reachable count and that
    criticals not proven reachable were not counted. Verdict is NOT altered."""
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(
            gate="fail",
            reason="1 reachable critical CVE detected",
            critical_cve_count=1,
            reachable_critical_cve_count=1,
            reachable_gate_enforced=True,
            reachable_relaxation_applied=True,
        ),
        summary=_make_summary(),
    )
    assert "**FAIL**" in body  # display-only, verdict unchanged
    assert "Reachable-only critical mode active" in body
    assert "1 reachable critical" in body
    assert "not proven reachable were not counted" in body


def test_markdown_shows_fallback_advisory_when_mode_had_no_effect() -> None:
    """Mode on but the scan has no reachability analysis (safe fallback) → the
    advisory says so explicitly, so nobody assumes a relaxed gate."""
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(
            gate="fail",
            reason="2 critical CVEs detected",
            critical_cve_count=2,
            reachable_critical_cve_count=0,
            reachable_gate_enforced=True,
            reachable_relaxation_applied=False,
        ),
        summary=_make_summary(),
    )
    assert "no reachability analysis" in body
    assert "evaluated all open criticals" in body


# ---------------------------------------------------------------------------
# Recommended upgrades section (v2.2 2.2-a3)
# ---------------------------------------------------------------------------


def test_markdown_omits_recommended_upgrades_when_empty() -> None:
    # The default summary carries no recommendations → no section.
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(),
        summary=_make_summary(),
    )
    assert "Recommended upgrades" not in body


def test_markdown_renders_recommended_upgrades() -> None:
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(),
        summary=_make_summary(
            recommended_upgrades=(
                RecommendedUpgrade(
                    component_name="log4j-core",
                    current_version="2.14.1",
                    recommended_version="2.17.1",
                    max_severity="critical",
                    direct=True,
                    cve_ids=("CVE-2021-44228", "CVE-2021-45046"),
                ),
                RecommendedUpgrade(
                    component_name="lodash",
                    current_version="4.17.11",
                    recommended_version="4.17.21",
                    max_severity="high",
                    direct=False,
                    cve_ids=("CVE-2019-10744",),
                ),
            ),
        ),
    )
    assert "### Recommended upgrades" in body
    # Component, current → recommended, severity tag, direct marker, CVE list.
    assert "`log4j-core`" in body
    assert "2.14.1 → **2.17.1**" in body
    assert "[critical]" in body
    assert "(direct)" in body
    assert "CVE-2021-44228" in body
    # The transitive row has no direct marker.
    assert "`lodash`" in body
    assert "4.17.11 → **4.17.21**" in body


def test_markdown_recommended_upgrade_without_cves_or_direct() -> None:
    body = build_pr_comment_markdown(
        gate_result=_make_gate_result(),
        summary=_make_summary(
            recommended_upgrades=(
                RecommendedUpgrade(
                    component_name="pkg",
                    current_version="1.0.0",
                    recommended_version="1.2.0",
                    max_severity="medium",
                    direct=False,
                    cve_ids=(),
                ),
            ),
        ),
    )
    assert "1.0.0 → **1.2.0**" in body
    assert "(direct)" not in body
    assert "fixes" not in body


# ---------------------------------------------------------------------------
# post_pr_comment — dry-run path
# ---------------------------------------------------------------------------


async def test_post_pr_comment_dry_run_returns_body_preview_no_token_required() -> None:
    posted = await post_pr_comment(
        repo_full_name="trustedoss/portal",
        pr_number=42,
        gate_result=_make_gate_result(),
        summary=_make_summary(),
        github_token=None,
        dry_run=True,
    )
    assert posted.status == "dry_run"
    assert posted.comment_id is None
    assert posted.comment_url is None
    assert COMMENT_MARKER in posted.body_preview or "TrustedOSS" in posted.body_preview


async def test_post_pr_comment_live_path_without_token_raises_unauthorized() -> None:
    with pytest.raises(SCACommentUnauthorized):
        await post_pr_comment(
            repo_full_name="trustedoss/portal",
            pr_number=42,
            gate_result=_make_gate_result(),
            summary=_make_summary(),
            github_token=None,
            dry_run=False,
        )


# ---------------------------------------------------------------------------
# post_pr_comment — create path
# ---------------------------------------------------------------------------


def _ok_json(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


async def test_post_pr_comment_creates_when_no_existing_marker() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        # GET list-comments — return empty
        if request.method == "GET" and "/issues/" in str(request.url):
            return _ok_json([])
        # POST create-comment
        if request.method == "POST" and "/comments" in str(request.url):
            return _ok_json(
                {
                    "id": 4242,
                    "html_url": "https://github.com/trustedoss/portal/issues/42#issuecomment-4242",
                },
                status_code=201,
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        posted = await post_pr_comment(
            repo_full_name="trustedoss/portal",
            pr_number=42,
            gate_result=_make_gate_result(),
            summary=_make_summary(),
            github_token="ghs_dummy",
            dry_run=False,
            http_client=client,
        )
    finally:
        await client.aclose()

    assert posted.status == "posted"
    assert posted.comment_id == 4242
    assert posted.comment_url and "issuecomment-4242" in posted.comment_url
    # First request lists comments, second creates one.
    assert captured[0].method == "GET"
    assert captured[1].method == "POST"
    # Body of the POST carries the marker.
    sent_body = json.loads(captured[1].content.decode("utf-8"))
    assert sent_body["body"].startswith(COMMENT_MARKER)
    # Auth header is set, token is forwarded but never echoed to logs.
    assert captured[1].headers["authorization"] == "Bearer ghs_dummy"


async def test_post_pr_comment_updates_existing_marker_comment() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            # Return one comment carrying the marker.
            return _ok_json(
                [
                    {"id": 999, "body": "irrelevant"},
                    {
                        "id": 7777,
                        "body": f"{COMMENT_MARKER}\nold body",
                        "html_url": (
                            "https://github.com/trustedoss/portal/issues/42"
                            "#issuecomment-7777"
                        ),
                    },
                ]
            )
        if request.method == "PATCH":
            return _ok_json(
                {
                    "id": 7777,
                    "html_url": (
                        "https://github.com/trustedoss/portal/issues/42#issuecomment-7777"
                    ),
                }
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        posted = await post_pr_comment(
            repo_full_name="trustedoss/portal",
            pr_number=42,
            gate_result=_make_gate_result(),
            summary=_make_summary(),
            github_token="ghs_dummy",
            dry_run=False,
            http_client=client,
        )
    finally:
        await client.aclose()

    assert posted.status == "updated"
    assert posted.comment_id == 7777
    # No POST issued — only GET + PATCH.
    methods = [r.method for r in captured]
    assert "POST" not in methods
    assert "PATCH" in methods


async def test_post_pr_comment_unauthorized_translates_to_401_domain_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SCACommentUnauthorized):
            await post_pr_comment(
                repo_full_name="trustedoss/portal",
                pr_number=42,
                gate_result=_make_gate_result(),
                summary=_make_summary(),
                github_token="ghs_dummy",
                dry_run=False,
                http_client=client,
            )
    finally:
        await client.aclose()


async def test_post_pr_comment_5xx_translates_to_bad_gateway() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SCACommentBadGateway):
            await post_pr_comment(
                repo_full_name="trustedoss/portal",
                pr_number=42,
                gate_result=_make_gate_result(),
                summary=_make_summary(),
                github_token="ghs_dummy",
                dry_run=False,
                http_client=client,
            )
    finally:
        await client.aclose()
