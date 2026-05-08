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
) -> GateResult:
    return GateResult(
        gate=gate,  # type: ignore[arg-type]
        reason=reason,
        critical_cve_count=critical_cve_count,
        forbidden_license_count=forbidden_license_count,
        project_id=uuid.uuid4(),
        scan_id=scan_id or uuid.uuid4(),
        evaluated_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
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
