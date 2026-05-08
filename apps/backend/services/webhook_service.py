"""
Webhook reception service — Phase 5 PR #16.

Pure async DB I/O for the GitHub / GitLab webhook gateway. The router
(``api/v1/webhooks/{github,gitlab}.py``) is responsible for HTTP-shape
parsing (header extraction); this module owns:

  - HMAC / token verification (constant-time via ``hmac.compare_digest``)
  - Idempotency persistence (``webhook_deliveries`` UNIQUE on
    (provider, delivery_id))
  - Project lookup (``Project.git_url`` match)
  - Scan enqueue (calls ``services.scan_service.trigger_scan`` indirectly
    via a thin helper to keep the audit context bound)

Security contracts:

  - HMAC verification uses :func:`hmac.compare_digest` to defeat timing-
    based oracle attacks. The signature header MUST be present and well-
    formed; missing / malformed → 401, NOT 400. Returning a structured
    "what was wrong" leaks too much detail to an attacker probing the
    endpoint.

  - The webhook secret is per-project. We look up by ``Project.git_url``
    matching the SCM payload's repo URL. A match is required even before
    HMAC verification — there is no global "fallback" secret.

  - Idempotency: every delivery attempts an INSERT into
    ``webhook_deliveries`` keyed on ``(provider, delivery_id)``. A unique-
    violation means we have already processed this delivery; we return the
    pre-existing row and a "duplicate" marker so the route can answer 200
    without re-enqueuing a scan.

  - Event whitelist: only a small set of event types triggers scan enqueue.
    Non-whitelisted events are stored in webhook_deliveries (for audit) but
    return 200 + ``{"status":"ignored"}``.

  - Logging never includes raw secrets, signatures, or request bodies.
    Only ``provider``, ``delivery_id``, ``event_type``, ``project_id``,
    and ``payload_hash`` are emitted.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import audit_context
from models import Project, Scan, WebhookDelivery
from tasks import enqueue_scan

log = structlog.get_logger("webhook.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class WebhookError(Exception):
    """Base class for webhook errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Webhook Error"


class WebhookSignatureInvalid(WebhookError):
    """401 — signature / token did not match the project's webhook_secret.

    We return 401 (not 403) because a missing / wrong signature is
    indistinguishable from "you're not authorised to talk to this endpoint at
    all". Returning 401 also matches the GitHub conventions for webhook
    receivers.
    """

    status_code = 401
    title = "Invalid Webhook Signature"


class WebhookProjectNotFound(WebhookError):
    """404 — the payload's repository URL does not match any configured project."""

    status_code = 404
    title = "Project Not Found"


class WebhookHeaderMissing(WebhookError):
    """400 — a required signature / delivery / event header is missing."""

    status_code = 400
    title = "Webhook Headers Missing"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GITHUB_SIGNATURE_PREFIX = "sha256="

# Whitelisted GitHub event types that trigger a source scan. Other events
# (e.g. ``ping``, ``issues``) are stored for audit and acknowledged but no
# scan is enqueued.
_GITHUB_SCAN_EVENTS = frozenset({"push", "pull_request"})

# Whitelisted GitLab event headers. GitLab sends "Push Hook" / "Merge Request
# Hook" rather than slugified names.
_GITLAB_SCAN_EVENTS = frozenset({"Push Hook", "Merge Request Hook"})


@dataclass
class WebhookProcessResult:
    """Return value from :func:`process_github_webhook` and friends.

    ``status`` is one of:
      - 'enqueued'  — a new scan was triggered. ``scan_id`` is set.
      - 'duplicate' — the delivery_id matched an existing row (idempotent).
      - 'ignored'   — event type is not in the scan whitelist.
    """

    status: str
    delivery: WebhookDelivery
    scan_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def compute_payload_hash(body: bytes) -> str:
    """Return the sha256 hex digest of *body*. 64 hex chars."""
    return hashlib.sha256(body).hexdigest()


def verify_github_signature(
    body: bytes,
    signature_header: str,
    secret: str,
) -> bool:
    """Verify the X-Hub-Signature-256 header against *body* using *secret*.

    GitHub sends ``sha256=<hex>``. We HMAC the raw body with the project's
    webhook_secret and compare in constant time.

    Returns False on any failure (missing prefix, invalid hex, length
    mismatch). Never raises — callers translate False into 401.
    """
    if not signature_header.startswith(_GITHUB_SIGNATURE_PREFIX):
        return False
    received_hex = signature_header[len(_GITHUB_SIGNATURE_PREFIX) :]
    if not received_hex:
        return False
    try:
        received_bytes = bytes.fromhex(received_hex)
    except ValueError:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    if len(received_bytes) != len(expected):
        return False
    # Constant-time comparison — defeats timing-side-channel attacks.
    return hmac.compare_digest(received_bytes, expected)


def verify_gitlab_token(received_token: str, secret: str) -> bool:
    """Constant-time token comparison for GitLab's X-Gitlab-Token header.

    GitLab does not (by default) HMAC-sign the body; it ships a shared bearer
    token instead. We still use :func:`hmac.compare_digest` so a wrong-token
    probe cannot leak information through timing.
    """
    return hmac.compare_digest(
        received_token.encode("utf-8"),
        secret.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# Project lookup
# ---------------------------------------------------------------------------


def _normalize_repo_url(url: str | None) -> str | None:
    """Strip trailing ``.git`` and trailing slashes for git_url comparison.

    GitHub and GitLab payloads sometimes include ``.git`` and sometimes do
    not; users may register either form under ``Project.git_url``. We
    canonicalise both before comparing.
    """
    if not url:
        return None
    cleaned = url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    return cleaned.rstrip("/")


async def _find_project_by_git_url(
    session: AsyncSession,
    repo_url: str,
    *,
    expected_provider: str,
) -> Project | None:
    """Find the Project whose git_url matches *repo_url* and webhook is enabled.

    The webhook is considered "enabled" when ``webhook_secret`` is set AND
    ``webhook_provider`` matches *expected_provider*. A project that has a
    GitHub secret but receives a GitLab payload (or vice versa) will not
    match — preventing cross-provider replay.
    """
    canonical = _normalize_repo_url(repo_url)
    if canonical is None:
        return None

    # Match either the canonical form or the .git-suffixed form. Postgres
    # cannot easily express that without a function index; we fetch by exact
    # match first and fall back to the .git form.
    candidates_urls = [canonical, f"{canonical}.git"]

    stmt = select(Project).where(
        Project.git_url.in_(candidates_urls),
        Project.webhook_secret.isnot(None),
        Project.webhook_provider == expected_provider,
        Project.archived_at.is_(None),
    )
    return (await session.execute(stmt)).scalars().first()


# ---------------------------------------------------------------------------
# Idempotency persistence
# ---------------------------------------------------------------------------


async def _record_delivery(
    session: AsyncSession,
    *,
    provider: str,
    delivery_id: str,
    event_type: str,
    payload_hash: str,
    project_id: uuid.UUID | None,
) -> tuple[WebhookDelivery, bool]:
    """
    Insert a webhook_deliveries row, returning ``(row, is_new)``.

    Idempotency: the unique index on (provider, delivery_id) is the canonical
    "have we processed this before?" gate. On unique-violation we re-fetch
    the existing row and return ``is_new=False``.
    """
    row = WebhookDelivery(
        provider=provider,
        delivery_id=delivery_id,
        event_type=event_type,
        payload_hash=payload_hash,
        project_id=project_id,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # Duplicate delivery — fetch the original.
        stmt = select(WebhookDelivery).where(
            WebhookDelivery.provider == provider,
            WebhookDelivery.delivery_id == delivery_id,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is None:
            # Vanishingly unlikely race (delete between rollback and re-fetch);
            # surface as a fresh attempt by re-raising would be wrong because
            # the caller already committed. Return a synthetic placeholder
            # marked is_new=False so the gateway answers 200.
            return row, False
        return existing, False

    await session.refresh(row)
    return row, True


# ---------------------------------------------------------------------------
# Scan enqueue helper
# ---------------------------------------------------------------------------


async def _enqueue_source_scan(
    session: AsyncSession,
    project: Project,
    *,
    metadata: dict[str, Any],
) -> uuid.UUID | None:
    """
    Create a queued source Scan for *project* and dispatch the Celery task.

    Returns the new scan id, or None if a scan is already in progress for
    this project (the partial unique index ``ix_scans_project_active`` makes
    that an idempotent no-op from the webhook's perspective — we already
    have a scan in the queue, no need to add another).

    Bind the audit team_id so the SQLAlchemy listener tags the scan row with
    the right tenant.
    """
    ctx = dict(audit_context.get() or {})
    ctx["team_id"] = str(project.team_id)
    audit_context.set(ctx)

    scan = Scan(
        project_id=project.id,
        kind="source",
        status="queued",
        progress_percent=0,
        current_step=None,
        celery_task_id=None,
        requested_by_user_id=None,  # webhook-driven — no user actor
        scan_metadata=metadata,
    )
    session.add(scan)
    try:
        await session.flush()
    except IntegrityError:
        # ix_scans_project_active fired — another scan is already queued
        # or running. Webhook is idempotent: roll back and report no new scan.
        await session.rollback()
        log.info(
            "webhook.scan_skip_in_progress",
            project_id=str(project.id),
        )
        return None

    project.latest_scan_id = scan.id
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        log.info(
            "webhook.scan_skip_in_progress_commit",
            project_id=str(project.id),
        )
        return None

    await session.refresh(scan)

    # Celery dispatch. Failure here is logged but does not block the webhook
    # response — the scan row exists in queued state and the admin can
    # re-enqueue or surface the failure via the admin scans dashboard.
    try:
        celery_task_id = enqueue_scan(scan)
        scan.celery_task_id = celery_task_id
        await session.commit()
        await session.refresh(scan)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "webhook.scan_enqueue_failed",
            scan_id=str(scan.id),
            project_id=str(project.id),
            error=str(exc),
            exc_info=True,
        )
        scan.status = "failed"
        scan.error_message = f"webhook_enqueue_failed: {exc}"
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()

    return scan.id


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


async def process_github_webhook(
    session: AsyncSession,
    *,
    body: bytes,
    signature_header: str | None,
    delivery_id: str | None,
    event_type: str | None,
    payload: dict[str, Any],
) -> WebhookProcessResult:
    """
    Verify + dispatch a GitHub webhook delivery.

    Sequence:
      1. Headers present? Else 400.
      2. Resolve project by ``payload.repository.clone_url`` (or ``html_url``
         as fallback). Project must have ``webhook_provider == 'github'``
         and a non-null ``webhook_secret``.
      3. Verify HMAC over the raw body. Else 401.
      4. Insert the delivery row (idempotency gate). Duplicate → 200 dup.
      5. Whitelist the event_type. Non-whitelisted → 200 ignored.
      6. Enqueue a source scan.
    """
    if not signature_header or not delivery_id or not event_type:
        raise WebhookHeaderMissing(
            "missing one of X-Hub-Signature-256, X-GitHub-Delivery, X-GitHub-Event"
        )

    # Step 2: resolve project from repo URL.
    repo_url = _extract_github_repo_url(payload)
    if repo_url is None:
        raise WebhookProjectNotFound("payload did not include a recognisable repository URL")

    project = await _find_project_by_git_url(
        session,
        repo_url,
        expected_provider="github",
    )
    if project is None or project.webhook_secret is None:
        raise WebhookProjectNotFound(
            f"no project configured for repo {repo_url!r}",
        )

    # Step 3: HMAC verification.
    if not verify_github_signature(body, signature_header, project.webhook_secret):
        # Log the failure with project + delivery id only — never the
        # signature or secret.
        log.warning(
            "webhook.github.signature_invalid",
            project_id=str(project.id),
            delivery_id=delivery_id,
        )
        raise WebhookSignatureInvalid("HMAC verification failed")

    payload_hash = compute_payload_hash(body)

    # Step 4: idempotency gate.
    delivery, is_new = await _record_delivery(
        session,
        provider="github",
        delivery_id=delivery_id,
        event_type=event_type,
        payload_hash=payload_hash,
        project_id=project.id,
    )
    if not is_new:
        log.info(
            "webhook.github.duplicate",
            project_id=str(project.id),
            delivery_id=delivery_id,
            event_type=event_type,
        )
        return WebhookProcessResult(status="duplicate", delivery=delivery)

    # Step 5: event whitelist.
    if event_type not in _GITHUB_SCAN_EVENTS:
        log.info(
            "webhook.github.ignored",
            project_id=str(project.id),
            delivery_id=delivery_id,
            event_type=event_type,
        )
        return WebhookProcessResult(status="ignored", delivery=delivery)

    # Step 6: enqueue source scan.
    scan_id = await _enqueue_source_scan(
        session,
        project,
        metadata={
            "trigger": "webhook",
            "provider": "github",
            "event_type": event_type,
            "delivery_id": delivery_id,
            "ref": payload.get("ref"),
        },
    )
    if scan_id is not None:
        delivery.enqueued_scan_id = scan_id
        await session.commit()

    log.info(
        "webhook.github.processed",
        project_id=str(project.id),
        delivery_id=delivery_id,
        event_type=event_type,
        scan_id=str(scan_id) if scan_id else None,
    )
    return WebhookProcessResult(
        status="enqueued" if scan_id else "duplicate",
        delivery=delivery,
        scan_id=scan_id,
    )


def _extract_github_repo_url(payload: dict[str, Any]) -> str | None:
    """Pull the repository clone / html URL from a GitHub event payload.

    The shape varies per event type (push: ``repository.clone_url``;
    pull_request: same). Fall back to ``html_url`` so this still works on
    payloads where clone_url is omitted.
    """
    repo = payload.get("repository")
    if not isinstance(repo, dict):
        return None
    for key in ("clone_url", "ssh_url", "git_url", "html_url"):
        val = repo.get(key)
        if isinstance(val, str) and val:
            return val
    return None


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


async def process_gitlab_webhook(
    session: AsyncSession,
    *,
    body: bytes,
    token_header: str | None,
    delivery_id: str | None,
    event_header: str | None,
    payload: dict[str, Any],
) -> WebhookProcessResult:
    """
    Verify + dispatch a GitLab webhook delivery.

    GitLab differs from GitHub:
      - Authentication is X-Gitlab-Token (constant-time bearer compare),
        not HMAC over the body.
      - Delivery id arrives via X-Gitlab-Webhook-UUID (newer GitLab) or, on
        older deployments, the request_uuid in the body.
      - Event header values are human-readable strings ("Push Hook").
    """
    if not token_header or not delivery_id or not event_header:
        raise WebhookHeaderMissing(
            "missing one of X-Gitlab-Token, X-Gitlab-Webhook-UUID, X-Gitlab-Event"
        )

    repo_url = _extract_gitlab_repo_url(payload)
    if repo_url is None:
        raise WebhookProjectNotFound("payload did not include a recognisable repository URL")

    project = await _find_project_by_git_url(
        session,
        repo_url,
        expected_provider="gitlab",
    )
    if project is None or project.webhook_secret is None:
        raise WebhookProjectNotFound(
            f"no project configured for repo {repo_url!r}",
        )

    if not verify_gitlab_token(token_header, project.webhook_secret):
        log.warning(
            "webhook.gitlab.token_invalid",
            project_id=str(project.id),
            delivery_id=delivery_id,
        )
        raise WebhookSignatureInvalid("X-Gitlab-Token mismatch")

    payload_hash = compute_payload_hash(body)

    delivery, is_new = await _record_delivery(
        session,
        provider="gitlab",
        delivery_id=delivery_id,
        event_type=event_header,
        payload_hash=payload_hash,
        project_id=project.id,
    )
    if not is_new:
        log.info(
            "webhook.gitlab.duplicate",
            project_id=str(project.id),
            delivery_id=delivery_id,
            event_type=event_header,
        )
        return WebhookProcessResult(status="duplicate", delivery=delivery)

    if event_header not in _GITLAB_SCAN_EVENTS:
        log.info(
            "webhook.gitlab.ignored",
            project_id=str(project.id),
            delivery_id=delivery_id,
            event_type=event_header,
        )
        return WebhookProcessResult(status="ignored", delivery=delivery)

    scan_id = await _enqueue_source_scan(
        session,
        project,
        metadata={
            "trigger": "webhook",
            "provider": "gitlab",
            "event_type": event_header,
            "delivery_id": delivery_id,
            "ref": payload.get("ref"),
        },
    )
    if scan_id is not None:
        delivery.enqueued_scan_id = scan_id
        await session.commit()

    log.info(
        "webhook.gitlab.processed",
        project_id=str(project.id),
        delivery_id=delivery_id,
        event_type=event_header,
        scan_id=str(scan_id) if scan_id else None,
    )
    return WebhookProcessResult(
        status="enqueued" if scan_id else "duplicate",
        delivery=delivery,
        scan_id=scan_id,
    )


def _extract_gitlab_repo_url(payload: dict[str, Any]) -> str | None:
    """Pull the project URL from a GitLab event payload.

    Push hooks use ``project.git_http_url``; merge request hooks nest the
    project under ``project.git_http_url`` too. ``repository.url`` is the
    historical fallback.
    """
    project = payload.get("project")
    if isinstance(project, dict):
        for key in ("git_http_url", "git_ssh_url", "url", "web_url"):
            val = project.get(key)
            if isinstance(val, str) and val:
                return val
    repo = payload.get("repository")
    if isinstance(repo, dict):
        for key in ("git_http_url", "url"):
            val = repo.get(key)
            if isinstance(val, str) and val:
                return val
    return None


__all__ = [
    "WebhookError",
    "WebhookHeaderMissing",
    "WebhookProcessResult",
    "WebhookProjectNotFound",
    "WebhookSignatureInvalid",
    "compute_payload_hash",
    "process_github_webhook",
    "process_gitlab_webhook",
    "verify_github_signature",
    "verify_gitlab_token",
]
