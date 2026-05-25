"""
SCA PR-comment service — Phase 5 PR #17.

Composes the Markdown body the portal posts to GitHub PRs and (optionally)
calls the GitHub API to create-or-update the comment.

Design choices
--------------
* **Stub-first**. PR #17 ships the comment *builder* and an HTTP client
  wrapper, but the default code path is ``dry_run`` — we render the Markdown
  and return it without touching api.github.com. Production wiring (App
  installation tokens, rate-limit handling, retries) lands in PR #18 alongside
  the GitHub Actions integration. Dry-run keeps the PR scope tight while
  still giving CI a concrete contract to integrate against.

* **Marker-based update**. Every comment we post starts with the HTML
  comment ``<!-- trustedoss-sca-bot -->``. When the same scan is re-evaluated
  (e.g. a force-push to the PR branch) the service finds the existing
  comment by this marker and PATCHes it instead of posting a new one. That
  avoids the "PR turns into a wall of bot noise" anti-pattern that early SCA
  tools shipped with.

* **Token never logged**. The GitHub token arrives via a function argument,
  is forwarded to ``Authorization: Bearer ...``, and never enters the
  structlog payload. The endpoint that calls into us reads the token from
  ``os.getenv()`` at request time (CLAUDE.md core rule #11) — module-level
  caching of secrets is forbidden.

* **No silent failures**. When the live HTTP path is exercised
  (``dry_run=False``) and the GitHub API returns a non-2xx, the function
  raises :class:`SCACommentError` with a problem-mappable status code. We do
  NOT swallow errors and return ``status='posted'`` with a null id — the CI
  runner needs to know whether the comment landed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog

from services.policy_gate import GateResult

log = structlog.get_logger("sca_comment.service")

GITHUB_API_BASE = "https://api.github.com"

# The marker is a literal HTML comment so it never renders, and it is unique
# enough that a search for it across other bots' comments returns no false
# positives. Do NOT change this string in a backwards-incompatible way: we
# rely on it to find the existing comment we previously posted.
COMMENT_MARKER = "<!-- trustedoss-sca-bot -->"

# Cap a single HTTP call at 10s. GitHub's PR-comment endpoint is consistently
# < 1s, so a 10x headroom is enough for transient blips without letting a
# slow upstream hold a worker hostage.
_HTTP_TIMEOUT_SECONDS = 10.0


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class SCACommentError(Exception):
    """Base class for SCA-comment errors. Each carries an HTTP status."""

    status_code: int = 502
    title: str = "PR Comment Error"


class SCACommentBadGateway(SCACommentError):
    """GitHub returned 5xx or the request never reached it."""

    status_code = 502
    title = "Bad Gateway (GitHub)"


class SCACommentUnauthorized(SCACommentError):
    """GitHub rejected the bearer token (401/403)."""

    status_code = 502  # surfaced as upstream-auth failure to the caller
    title = "GitHub Authentication Failed"


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecommendedUpgrade:
    """One "bump this dependency" row the comment surfaces (v2.2 2.2-a3).

    Built by the router from the scan's open findings + the upgrade
    recommendation engine. The service stays pure (no DB access). ``cve_ids``
    is a short list of the CVEs the upgrade resolves for the component (capped
    by the router so the comment never explodes).
    """

    component_name: str
    current_version: str
    recommended_version: str
    max_severity: str
    direct: bool
    cve_ids: tuple[str, ...]


@dataclass(frozen=True)
class CommentSummary:
    """Numbers the Markdown body needs beyond the gate result.

    Built by the router from the project's latest scan so the service stays
    pure (no DB access here).
    """

    components_count: int
    severity_distribution: dict[str, int]
    license_distribution: dict[str, int]
    project_url: str | None = None
    # v2.2 2.2-a3 — the highest-priority "upgrade to X" recommendations for this
    # scan, already sorted (most urgent first) and capped by the router. Empty
    # when no actionable upgrade was found; the comment then omits the section.
    recommended_upgrades: tuple[RecommendedUpgrade, ...] = ()


def _format_severity_line(label: str, glyph: str, count: int) -> str:
    """Render one severity bullet. Lower-case label, count right-aligned."""
    return f"- {glyph} **{label}**: {count}"


def _gate_badge(gate: str) -> str:
    """ASCII badge for the gate verdict — no emoji to keep output portable."""
    if gate == "fail":
        return "FAIL"
    return "PASS"


def _reachable_mode_advisory(gate_result: GateResult) -> str | None:
    """Advisory line surfacing the reachable-only critical mode (display only).

    Security-reviewer fix-first (Medium #2): when the opt-in
    ``GATE_REACHABLE_CRITICAL_ONLY`` mode is enabled, a reviewer reading the PR
    comment must be able to SEE that the critical verdict may have been narrowed
    to reachable findings — otherwise the relaxation is invisible. This NEVER
    changes the verdict; it only annotates it.

    Two truthful states:
      * The mode was enabled AND actually took effect (the scan was
        reachability-analysed): tell the reviewer that criticals not proven
        reachable were not counted, and how many reachable criticals remain.
      * The mode was enabled but had NO effect (safe-by-default fallback: the
        scan has no reachability analysis — e.g. a non-Go ecosystem): say so
        explicitly, so nobody assumes a relaxed gate when the gate actually ran
        at full strength.

    Returns None when the mode is off (``reachable_gate_enforced`` False), so the
    legacy comment body is byte-for-byte unchanged.
    """
    if not gate_result.reachable_gate_enforced:
        return None
    if gate_result.reachable_relaxation_applied:
        n = gate_result.reachable_critical_cve_count
        noun = "critical" if n == 1 else "criticals"
        return (
            f"> Reachable-only critical mode active — {n} reachable {noun}; "
            "criticals not proven reachable were not counted toward the gate."
        )
    return (
        "> Reachable-only critical mode requested, but this scan has no "
        "reachability analysis — the gate evaluated all open criticals."
    )


def build_pr_comment_markdown(
    *,
    gate_result: GateResult,
    summary: CommentSummary,
) -> str:
    """Render the Markdown body of the SCA comment.

    The output begins with :data:`COMMENT_MARKER` so the create-or-update
    path can find an existing comment without parsing user-controlled
    content.
    """
    sev = summary.severity_distribution
    lic = summary.license_distribution

    lines: list[str] = []
    lines.append(COMMENT_MARKER)
    lines.append("## TrustedOSS SCA Report")
    lines.append("")
    lines.append(f"**Gate**: **{_gate_badge(gate_result.gate)}**")
    if gate_result.reason:
        lines.append(f"**Reason**: {gate_result.reason}")
    advisory = _reachable_mode_advisory(gate_result)
    if advisory:
        lines.append(advisory)
    lines.append("")

    lines.append(f"**Components scanned**: {summary.components_count}")
    lines.append("")

    lines.append("### Vulnerabilities")
    lines.append(_format_severity_line("Critical", "[!]", int(sev.get("critical", 0))))
    lines.append(_format_severity_line("High", "[H]", int(sev.get("high", 0))))
    lines.append(_format_severity_line("Medium", "[M]", int(sev.get("medium", 0))))
    lines.append(_format_severity_line("Low", "[L]", int(sev.get("low", 0))))
    lines.append("")

    lines.append("### Licenses")
    lines.append(f"- Allowed: {int(lic.get('allowed', 0))}")
    lines.append(f"- Conditional: {int(lic.get('conditional', 0))}")
    lines.append(f"- Forbidden: {int(lic.get('forbidden', 0))}")
    lines.append("")

    # v2.2 2.2-a3 — recommended upgrades ("finding → action"). Only rendered
    # when the engine found at least one actionable upgrade so a clean PR
    # doesn't carry an empty section.
    if summary.recommended_upgrades:
        lines.append("### Recommended upgrades")
        for rec in summary.recommended_upgrades:
            marker = " (direct)" if rec.direct else ""
            cve_note = ""
            if rec.cve_ids:
                cve_note = f" — fixes {', '.join(rec.cve_ids)}"
            # `component current → recommended` with a severity tag. All values
            # are package/version/CVE-id tokens (no untrusted free text).
            lines.append(
                f"- `{rec.component_name}` "
                f"{rec.current_version} → **{rec.recommended_version}** "
                f"[{rec.max_severity}]{marker}{cve_note}"
            )
        lines.append("")

    if summary.project_url:
        # Markdown auto-links bare URLs, but an explicit anchor reads better
        # in PR review tools that don't auto-link.
        lines.append(f"[View full report]({summary.project_url})")
        lines.append("")

    return "\n".join(lines)


def _preview(body: str, *, max_chars: int = 280) -> str:
    """First N chars of ``body`` for response payloads / logs."""
    if len(body) <= max_chars:
        return body
    # Trim on a whitespace boundary so the preview is a clean prefix.
    cut = body[:max_chars]
    space = cut.rfind(" ")
    if space > max_chars * 0.6:
        cut = cut[:space]
    return cut + "..."


# ---------------------------------------------------------------------------
# GitHub API client
# ---------------------------------------------------------------------------


def _auth_headers(token: str) -> dict[str, str]:
    """Standard GitHub REST headers. Token is in-memory only."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "trustedoss-sca-bot",
    }


async def _find_existing_comment(
    client: httpx.AsyncClient,
    *,
    repo_full_name: str,
    pr_number: int,
    token: str,
) -> dict[str, Any] | None:
    """Page through PR comments looking for one carrying our marker.

    GitHub paginates issue comments at 100/page; for PRs with pathological
    bot noise we cap at 5 pages (500 comments) — beyond that we fall back to
    creating a new comment rather than scanning indefinitely.
    """
    for page in range(1, 6):
        url = (
            f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments"
            f"?per_page=100&page={page}"
        )
        try:
            response = await client.get(url, headers=_auth_headers(token))
        except httpx.HTTPError as exc:
            raise SCACommentBadGateway(f"github get-comments failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise SCACommentUnauthorized(
                f"github rejected token while listing comments: {response.status_code}",
            )
        if response.status_code >= 500:
            raise SCACommentBadGateway(
                f"github returned {response.status_code} listing comments",
            )
        if response.status_code == 404:
            # PR or repo not visible — propagate as unauthorized so the caller
            # can surface a single "GitHub auth failed" message.
            raise SCACommentUnauthorized(
                "github returned 404 listing comments — token lacks read access",
            )
        if response.status_code != 200:
            raise SCACommentBadGateway(
                f"github returned unexpected {response.status_code} listing comments",
            )

        items = response.json()
        if not isinstance(items, list):
            raise SCACommentBadGateway("github comments response was not a list")
        for entry in items:
            body = (entry or {}).get("body") or ""
            if isinstance(body, str) and body.startswith(COMMENT_MARKER):
                return dict(entry)
        if len(items) < 100:
            return None
    return None


async def _create_comment(
    client: httpx.AsyncClient,
    *,
    repo_full_name: str,
    pr_number: int,
    body: str,
    token: str,
) -> dict[str, Any]:
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments"
    try:
        response = await client.post(
            url,
            headers=_auth_headers(token),
            json={"body": body},
        )
    except httpx.HTTPError as exc:
        raise SCACommentBadGateway(f"github post-comment failed: {exc}") from exc

    if response.status_code in (401, 403):
        raise SCACommentUnauthorized(
            f"github rejected token while posting comment: {response.status_code}",
        )
    if response.status_code >= 500:
        raise SCACommentBadGateway(
            f"github returned {response.status_code} posting comment",
        )
    if response.status_code not in (200, 201):
        raise SCACommentBadGateway(
            f"github returned unexpected {response.status_code} posting comment",
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise SCACommentBadGateway("github post-comment response was not an object")
    return payload


async def _update_comment(
    client: httpx.AsyncClient,
    *,
    repo_full_name: str,
    comment_id: int,
    body: str,
    token: str,
) -> dict[str, Any]:
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/comments/{comment_id}"
    try:
        response = await client.patch(
            url,
            headers=_auth_headers(token),
            json={"body": body},
        )
    except httpx.HTTPError as exc:
        raise SCACommentBadGateway(f"github patch-comment failed: {exc}") from exc

    if response.status_code in (401, 403):
        raise SCACommentUnauthorized(
            f"github rejected token while updating comment: {response.status_code}",
        )
    if response.status_code >= 500:
        raise SCACommentBadGateway(
            f"github returned {response.status_code} updating comment",
        )
    if response.status_code != 200:
        raise SCACommentBadGateway(
            f"github returned unexpected {response.status_code} updating comment",
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise SCACommentBadGateway("github patch-comment response was not an object")
    return payload


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostedComment:
    """Outcome of a posted (or simulated) PR comment."""

    status: Literal["posted", "updated", "dry_run"]
    comment_id: int | None
    comment_url: str | None
    body_preview: str


async def post_pr_comment(
    *,
    repo_full_name: str,
    pr_number: int,
    gate_result: GateResult,
    summary: CommentSummary,
    github_token: str | None,
    dry_run: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> PostedComment:
    """Render the SCA Markdown comment and (optionally) post it to GitHub.

    Parameters
    ----------
    repo_full_name:
        ``owner/repo``. The schema layer validates the shape; we trust it
        here.
    pr_number:
        GitHub PR number.
    gate_result:
        Verdict produced by :func:`services.policy_gate.evaluate_gate`.
    summary:
        Component / severity / license counts the comment body needs.
    github_token:
        Bearer token used for ``Authorization``. Required when
        ``dry_run=False``; ignored otherwise. NEVER logged.
    dry_run:
        When ``True`` (default in tests) we render the body and return it
        without calling GitHub. The default in production is ``False``.
    http_client:
        Optional injected client. The integration tests pass an
        ``httpx.MockTransport`` here so they can assert the request shape
        without going to the network.
    """
    body = build_pr_comment_markdown(gate_result=gate_result, summary=summary)
    body_preview = _preview(body)

    if dry_run:
        log.info(
            "sca_comment.dry_run",
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            gate=gate_result.gate,
            body_chars=len(body),
        )
        return PostedComment(
            status="dry_run",
            comment_id=None,
            comment_url=None,
            body_preview=body_preview,
        )

    if not github_token:
        # Caller must provide a token for the live path. We do not surface
        # the absence of the env var in the response — only that a token was
        # required.
        raise SCACommentUnauthorized("github token required for non-dry-run posts")

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)
    try:
        existing = await _find_existing_comment(
            client,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            token=github_token,
        )

        if existing is not None:
            comment_id = int(existing["id"])
            updated = await _update_comment(
                client,
                repo_full_name=repo_full_name,
                comment_id=comment_id,
                body=body,
                token=github_token,
            )
            log.info(
                "sca_comment.updated",
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                comment_id=comment_id,
                gate=gate_result.gate,
            )
            return PostedComment(
                status="updated",
                comment_id=int(updated.get("id", comment_id)),
                comment_url=str(updated.get("html_url") or existing.get("html_url") or ""),
                body_preview=body_preview,
            )

        created = await _create_comment(
            client,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            body=body,
            token=github_token,
        )
        log.info(
            "sca_comment.posted",
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            comment_id=created.get("id"),
            gate=gate_result.gate,
        )
        new_id = created.get("id")
        return PostedComment(
            status="posted",
            comment_id=int(new_id) if isinstance(new_id, int) else None,
            comment_url=str(created.get("html_url") or ""),
            body_preview=body_preview,
        )
    finally:
        if owns_client:
            await client.aclose()


__all__ = [
    "COMMENT_MARKER",
    "CommentSummary",
    "PostedComment",
    "RecommendedUpgrade",
    "SCACommentBadGateway",
    "SCACommentError",
    "SCACommentUnauthorized",
    "build_pr_comment_markdown",
    "post_pr_comment",
]
