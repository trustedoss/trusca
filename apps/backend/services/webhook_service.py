# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.pii_mask import mask_git_url
from models import Project, WebhookDelivery
from services.scan_service import (
    capacity_guard_reason,
    enqueue_system_triggered_scan_async,
)

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

# Which ``pull_request`` actions are worth a scan. GitHub sends this event for
# roughly a dozen actions — labelled, assigned, review requested, closed — and
# the dependency set only changes on the three below. Scanning the rest burnt
# worker capacity and, because one active scan is allowed per (project, ref),
# a stray `labeled` could hold the slot a real push then collided with.
_GITHUB_PR_SCAN_ACTIONS = frozenset({"opened", "synchronize", "reopened"})

# Whitelisted GitLab event headers. GitLab sends "Push Hook" / "Merge Request
# Hook" rather than slugified names.
_GITLAB_SCAN_EVENTS = frozenset({"Push Hook", "Merge Request Hook"})

# The GitLab counterpart. ``update`` is the push-to-source-branch action; the
# others mirror GitHub's opened / reopened.
_GITLAB_MR_SCAN_ACTIONS = frozenset({"open", "reopen", "update"})

# Every value ``WebhookProcessResult.status`` can take. The router's response
# documentation mirrors this set, and ``test_webhook_status_vocabulary`` asserts
# the two agree — the vocabulary lives in two places by necessity (one is the
# OpenAPI contract, the other the implementation) and drifted once already.
WEBHOOK_STATUSES = frozenset(
    {
        "enqueued",
        "duplicate",
        "ignored",
        "skipped_active_scan",
        "skipped_team_at_capacity",
        "skipped_disk_full",
    }
)

# What ``webhook_deliveries.outcome`` can hold: every status except
# ``duplicate``, which describes a request rather than a delivery. A replayed
# delivery keeps the outcome it earned the first time.
WEBHOOK_OUTCOMES = WEBHOOK_STATUSES - {"duplicate"}

# Outcomes a redelivery is allowed to supersede: the ones where no scan ever
# started AND the condition is transient. The operator frees capacity and
# redelivers; turning that away as a duplicate would leave the push unscanned
# forever. ``ignored`` and ``skipped_active_scan`` are NOT here: an ignored
# event will be ignored again, and an active scan on the ref means the commit
# is already being covered.
_SUPERSEDABLE_OUTCOMES = frozenset(
    {
        "skipped_team_at_capacity",
        "skipped_disk_full",
    }
)


@dataclass
class WebhookProcessResult:
    """Return value from :func:`process_github_webhook` and friends.

    ``status`` is one of:
      - 'enqueued'            — a new scan was triggered. ``scan_id`` is set.
      - 'duplicate'           — the delivery_id matched an existing row
                                (idempotent replay of a delivery we already saw).
      - 'ignored'             — the event is not one we scan on: either the type
                                is outside the whitelist, or it is a pull request
                                action that cannot change the dependency set.
      - 'skipped_active_scan' — the delivery was new and scannable, but a scan
                                for the same ref was already queued or running,
                                so we did not start a second one.
      - 'skipped_team_at_capacity'
                              — the owning team is at its concurrent-scan cap.
      - 'skipped_disk_full'   — the workspace volume is over its hard limit.

    'skipped_active_scan' used to be reported as 'duplicate' too, which was
    actively misleading: 'duplicate' means "we have seen this delivery", so an
    operator reading the SCM's delivery log had no way to tell that a push had
    gone unscanned. They are different events and now say so.

    The two capacity values are reported rather than raised on purpose. A 4xx
    or 5xx would make the Git host retry a delivery that cannot succeed until
    the operator frees capacity, and the retry storm would land on the system
    that is already under pressure.

    Every ending is recorded on the delivery row's ``outcome`` (gap #39). The
    capacity skips used to be decided before the row was written, so they left
    no database trace at all: the reason was that claiming the delivery id
    would turn a later redelivery into a duplicate and the push would never be
    scanned, even once capacity freed up. That is still the requirement, but it
    is now met by letting a redelivery SUPERSEDE a row whose outcome says no
    scan ever started, rather than by not writing the row. The id names the
    delivery's current state, not a counter that gets spent.

    On GitLab installs that do not send a delivery UUID the id is derived from
    the payload and is therefore stable, which is what made the old loss
    permanent and this recovery reliable.
    """

    status: str
    delivery: WebhookDelivery | None
    scan_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


# Cap on the repository URL we are willing to write to the log. The value is
# attacker-controlled and unbounded: ``_normalize_repo_url`` strips trailing
# slashes and whitespace, so a caller can append a megabyte of padding to a URL
# and still have it match a configured project.
#
# That matters because the log is the ONLY asymmetry left between "no such
# repository" and "bad signature", and asymmetric work is a timing oracle —
# which is exactly what answering both with 401 was meant to remove. Logging a
# megabyte on one branch and a UUID on the other hands the distinction straight
# back, at a magnitude the caller chooses. Truncating bounds that, and bounds
# what an unauthenticated caller can write to disk.
_LOGGED_REPO_URL_MAX = 200


def _loggable_repo_url(repo_url: str) -> str:
    """Bound and redact a payload-supplied repository URL for the log.

    Cuts to a generous bound BEFORE masking, not after. ``mask_git_url`` parses
    the whole string, so masking first would leave this branch doing work
    proportional to an attacker-chosen length — the very asymmetry the cap
    exists to remove. Slicing first makes the extra cost of the unmatched path
    constant.

    The pre-cut is far larger than the final one so it cannot change what gets
    masked: userinfo sits at the front of a URL, well inside the first slice.
    """
    masked = mask_git_url(repo_url[: _LOGGED_REPO_URL_MAX * 20]) or ""
    if len(masked) > _LOGGED_REPO_URL_MAX:
        return f"{masked[:_LOGGED_REPO_URL_MAX]}…(truncated)"
    return masked


def _reject_unmatched_github_delivery(
    *,
    body: bytes,
    signature_header: str,
    repo_url: str,
    delivery_id: str | None,
) -> NoReturn:
    """Reject a delivery whose repository is not configured here, as a 401.

    Deliberately indistinguishable from a signature failure on the wire, so the
    endpoint cannot be used to enumerate which repositories a portal watches.
    The server log keeps the distinction, because an operator debugging a
    genuinely mistyped URL needs it and the log is not reachable by the caller.

    Runs a real HMAC against a throwaway key first: the comparison can never
    succeed, and it keeps the rejected path doing similar work to the accepted
    one. Never returns.
    """
    verify_github_signature(body, signature_header, secrets.token_urlsafe(32))
    log.warning(
        "webhook.unknown_repository",
        provider="github",
        repo_url=_loggable_repo_url(repo_url),
        delivery_id=delivery_id,
    )
    raise WebhookSignatureInvalid("HMAC verification failed")


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

    Defensive: reject URLs containing NUL bytes or other ASCII control bytes.
    Postgres ``text``/``varchar`` columns cannot encode 0x00 (asyncpg raises
    ``CharacterNotInRepertoireError``), and CR/LF in a header-derived URL is a
    response-splitting smell. A payload with such bytes cannot match any
    legitimate ``Project.git_url`` row, so we return ``None`` and let the
    caller surface a clean 404 rather than a 500.
    """
    if not url:
        return None
    cleaned = url.strip()
    # NUL or any C0 control byte (except whitespace already stripped) — reject.
    if any(ch == "\x00" or (ord(ch) < 0x20 and ch not in ("\t",)) for ch in cleaned):
        return None
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
    "have we processed this before?" gate. On unique-violation we re-fetch the
    existing row.

    ``is_new`` is True for a row this call created, and ALSO for one it
    superseded: a delivery whose recorded outcome says no scan ever started and
    the reason was transient (see ``_SUPERSEDABLE_OUTCOMES``). The gate exists
    to stop one delivery being scanned twice, and a delivery that was turned
    away at the capacity guard has not been scanned once. Redelivering it is
    the documented recovery, so it has to run the pipeline again rather than
    read as a duplicate.
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
        # Already seen: fetch the original and decide whether it is a
        # duplicate or a retry of something that never ran.
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
        if existing.outcome in _SUPERSEDABLE_OUTCOMES:
            # Same delivery, new attempt. Refresh what the payload can change
            # (a redelivery carries the same body, but the project may have
            # been re-pointed) and hand it back as if newly recorded.
            existing.event_type = event_type
            existing.payload_hash = payload_hash
            existing.project_id = project_id
            existing.outcome = None
            existing.received_at = _now()
            # Flush rather than commit: the caller carries on in this same
            # transaction, and a commit here would expire every ORM instance
            # it still needs.
            await session.flush()
            return existing, True
        return existing, False

    await session.refresh(row)
    return row, True


async def _finish_delivery(
    session: AsyncSession,
    delivery: WebhookDelivery | None,
    outcome: str,
    *,
    scan_id: uuid.UUID | None = None,
) -> None:
    """Stamp how the delivery ended, so a SELECT can answer it later (gap #39).

    Best-effort by design: the caller has already decided what to report, and
    failing to record the label must not change that answer or turn a 200 into
    a 500 the Git host would retry.
    """
    if delivery is None:
        return
    delivery.outcome = outcome
    if scan_id is not None:
        delivery.enqueued_scan_id = scan_id
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - observability must not break delivery
        await session.rollback()
        log.warning(
            "webhook.outcome_not_recorded",
            delivery_id=delivery.delivery_id,
            outcome=outcome,
            error=str(exc)[:200],
        )


# ---------------------------------------------------------------------------
# Scan enqueue helper
#
# The actual create-scan-and-dispatch sequence now lives in
# ``services.scan_service.enqueue_system_triggered_scan_async`` — promoted
# there so a second "no actor" caller (the N18 scheduled-scan poller) reuses
# the SAME guard-and-insert sequence instead of a third copy of it. This
# module keeps only the thin alias its two call sites already use.
# ---------------------------------------------------------------------------

_enqueue_source_scan = enqueue_system_triggered_scan_async


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
        # Answer exactly as a bad signature does. The lookup has to precede
        # verification (the secret is per-project), so the response code was
        # the only thing separating "this repo is configured here" from "it is
        # not — and an unauthenticated caller could walk a list of repository
        # URLs and read that difference off the status code.
        #
        # The HMAC still runs, against a throwaway key that cannot match, so
        # the two paths do comparable work. This does not equalise the database
        # lookup itself, so it closes the status-code oracle rather than every
        # timing signal; the former is trivially observable and the latter is
        # not.
        _reject_unmatched_github_delivery(
            body=body,
            signature_header=signature_header,
            repo_url=repo_url,
            delivery_id=delivery_id,
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
    # Capture project.id BEFORE _record_delivery — that helper may rollback on
    # a duplicate-delivery IntegrityError, which expires every ORM object in
    # the session. Accessing ``project.id`` post-rollback would then trigger a
    # synchronous lazy reload from outside the greenlet context and surface
    # as ``MissingGreenlet`` (regression seen in Chore L2 duplicate-delivery
    # tests). The id is a stable UUID, so caching is safe.
    project_id_str = str(project.id)
    # Same reason, and now load-bearing for the capacity guard too: the guard
    # runs AFTER _record_delivery, whose commit expires every ORM attribute on
    # this Project. Reading team_id off the instance at that point triggers a
    # synchronous lazy reload outside the greenlet and raises MissingGreenlet.
    project_team_id = project.team_id

    # Step 4: idempotency gate.
    delivery, is_new = await _record_delivery(
        session,
        provider="github",
        delivery_id=delivery_id,
        event_type=event_type,
        payload_hash=payload_hash,
        project_id=project.id,
    )
    # A delivery we had seen before reaches _record_delivery's rollback
    # path, which expires every ORM instance in the session, and the rest
    # of this function reads `project`. Reload it before going on.
    await session.refresh(project)

    if not is_new:
        log.info(
            "webhook.github.duplicate",
            project_id=project_id_str,
            delivery_id=delivery_id,
            event_type=event_type,
        )
        # Deliberately NOT stamped: "duplicate" is this request's answer, not
        # the delivery's ending. The row already records what it achieved the
        # first time, and overwriting that would lose it.
        return WebhookProcessResult(status="duplicate", delivery=delivery)

    # Step 5: event whitelist, then the per-action filter for pull requests.
    # Recorded first so an ignored delivery still leaves an audit trail.
    if not _github_event_is_scannable(payload, event_type):
        log.info(
            "webhook.github.ignored",
            project_id=project_id_str,
            delivery_id=delivery_id,
            event_type=event_type,
            action=payload.get("action"),
        )
        await _finish_delivery(session, delivery, "ignored")
        return WebhookProcessResult(status="ignored", delivery=delivery)

    # Step 6: capacity guards. Asked only for events that would actually scan,
    # so a `ping` is never reported as "skipped because the disk is full". The
    # delivery row is already written, and a redelivery supersedes it once the
    # operator frees capacity (see _record_delivery).
    capacity_block = await capacity_guard_reason(session, team_id=project_team_id)
    if capacity_block is not None:
        log.warning(
            "webhook.github.capacity_skip",
            project_id=project_id_str,
            delivery_id=delivery_id,
            reason=capacity_block,
        )
        await _finish_delivery(session, delivery, capacity_block)
        return WebhookProcessResult(status=capacity_block, delivery=delivery)

    # Step 7: enqueue source scan.
    scan_id = await _enqueue_source_scan(
        session,
        project,
        metadata={
            "trigger": "webhook",
            # ``source`` names the same thing the CI clients put here; the two
            # halves of the system used different keys for one concept.
            "source": "webhook-github",
            "provider": "github",
            "event_type": event_type,
            "delivery_id": delivery_id,
            "ref": _github_ref(payload, event_type),
        },
    )
    status = "enqueued" if scan_id else "skipped_active_scan"
    await _finish_delivery(session, delivery, status, scan_id=scan_id)

    log.info(
        "webhook.github.processed",
        project_id=project_id_str,
        delivery_id=delivery_id,
        event_type=event_type,
        scan_id=str(scan_id) if scan_id else None,
    )
    return WebhookProcessResult(
        status=status,
        delivery=delivery,
        scan_id=scan_id,
    )


def _github_ref(payload: dict[str, Any], event_type: str) -> str | None:
    """Return the ref this GitHub event targets, in a form the normalizer knows.

    ``payload["ref"]`` is a top-level field on ``push`` only. Reading it for
    ``pull_request`` produced None, so every webhook-triggered PR scan landed in
    the project's ad-hoc (ref-less) cohort: it never superseded the previous
    scan of that PR, never grouped with the same PR's action-triggered scans,
    and collided with unrelated ref-less scans over the one active-scan slot.

    ``refs/pull/<n>/merge`` is the same long form the GitHub action sends, so
    both paths normalize to ``pr-<n>`` and describe one target.
    """
    if event_type == "pull_request":
        number = (payload.get("pull_request") or {}).get("number")
        if isinstance(number, int):
            return f"refs/pull/{number}/merge"
        return None
    ref = payload.get("ref")
    return ref if isinstance(ref, str) else None


def _gitlab_ref(payload: dict[str, Any], event_header: str) -> str | None:
    """GitLab's counterpart to :func:`_github_ref`.

    Merge request hooks carry no top-level ``ref`` either; the IID lives under
    ``object_attributes``. ``refs/merge-requests/<iid>/head`` normalizes to
    ``mr-<iid>``.
    """
    if event_header == "Merge Request Hook":
        iid = (payload.get("object_attributes") or {}).get("iid")
        if isinstance(iid, int):
            return f"refs/merge-requests/{iid}/head"
        return None
    ref = payload.get("ref")
    return ref if isinstance(ref, str) else None


def _github_event_is_scannable(payload: dict[str, Any], event_type: str) -> bool:
    """True when this delivery would enqueue a scan.

    Asked twice per delivery — once to decide whether the capacity guards are
    even relevant, once to decide the outcome — so it lives in one place rather
    than being spelled out at both call sites, where the two copies could drift
    and let a `ping` be reported as skipped for capacity.
    """
    if event_type not in _GITHUB_SCAN_EVENTS:
        return False
    if event_type == "pull_request":
        return _github_pr_action_is_scannable(payload)
    return True


def _gitlab_event_is_scannable(payload: dict[str, Any], event_header: str) -> bool:
    """GitLab's counterpart to :func:`_github_event_is_scannable`."""
    if event_header not in _GITLAB_SCAN_EVENTS:
        return False
    if event_header == "Merge Request Hook":
        return _gitlab_mr_action_is_scannable(payload)
    return True


def _github_pr_action_is_scannable(payload: dict[str, Any]) -> bool:
    """True when this ``pull_request`` action can have changed dependencies.

    An absent or non-string action is treated as scannable: an unfamiliar
    payload shape should scan rather than silently skip, since missing a real
    change is worse than one redundant scan.
    """
    action = payload.get("action")
    if not isinstance(action, str):
        return True
    return action in _GITHUB_PR_SCAN_ACTIONS


def _gitlab_mr_action_is_scannable(payload: dict[str, Any]) -> bool:
    """GitLab's counterpart. Same permissive default for unknown shapes."""
    action = (payload.get("object_attributes") or {}).get("action")
    if not isinstance(action, str):
        return True
    return action in _GITLAB_MR_SCAN_ACTIONS


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
        # Same reasoning as the GitHub path: answer as a token mismatch so the
        # status code cannot be read as "this repository is configured here".
        verify_gitlab_token(token_header, secrets.token_urlsafe(32))
        log.warning(
            "webhook.unknown_repository",
            provider="gitlab",
            repo_url=_loggable_repo_url(repo_url),
            delivery_id=delivery_id,
        )
        raise WebhookSignatureInvalid("X-Gitlab-Token mismatch")

    if not verify_gitlab_token(token_header, project.webhook_secret):
        log.warning(
            "webhook.gitlab.token_invalid",
            project_id=str(project.id),
            delivery_id=delivery_id,
        )
        raise WebhookSignatureInvalid("X-Gitlab-Token mismatch")

    payload_hash = compute_payload_hash(body)
    # See process_github_webhook — capture the project id before _record_delivery
    # may rollback and expire ORM state.
    project_id_str = str(project.id)
    # See the GitHub twin: the capacity guard reads this after _record_delivery
    # has expired the instance's attributes.
    project_team_id = project.team_id

    delivery, is_new = await _record_delivery(
        session,
        provider="gitlab",
        delivery_id=delivery_id,
        event_type=event_header,
        payload_hash=payload_hash,
        project_id=project.id,
    )
    # A delivery we had seen before reaches _record_delivery's rollback
    # path, which expires every ORM instance in the session, and the rest
    # of this function reads `project`. Reload it before going on.
    await session.refresh(project)

    if not is_new:
        log.info(
            "webhook.gitlab.duplicate",
            project_id=project_id_str,
            delivery_id=delivery_id,
            event_type=event_header,
        )
        # See the GitHub twin: a replay does not overwrite the original ending.
        return WebhookProcessResult(status="duplicate", delivery=delivery)

    if not _gitlab_event_is_scannable(payload, event_header):
        log.info(
            "webhook.gitlab.ignored",
            project_id=project_id_str,
            delivery_id=delivery_id,
            event_type=event_header,
            action=(payload.get("object_attributes") or {}).get("action"),
        )
        await _finish_delivery(session, delivery, "ignored")
        return WebhookProcessResult(status="ignored", delivery=delivery)

    # Capacity guards. See the GitHub twin: the row is written first and a
    # redelivery supersedes it once capacity frees up.
    capacity_block = await capacity_guard_reason(session, team_id=project_team_id)
    if capacity_block is not None:
        log.warning(
            "webhook.gitlab.capacity_skip",
            project_id=project_id_str,
            delivery_id=delivery_id,
            reason=capacity_block,
        )
        await _finish_delivery(session, delivery, capacity_block)
        return WebhookProcessResult(status=capacity_block, delivery=delivery)

    scan_id = await _enqueue_source_scan(
        session,
        project,
        metadata={
            "trigger": "webhook",
            "source": "webhook-gitlab",
            "provider": "gitlab",
            "event_type": event_header,
            "delivery_id": delivery_id,
            "ref": _gitlab_ref(payload, event_header),
        },
    )
    status = "enqueued" if scan_id else "skipped_active_scan"
    await _finish_delivery(session, delivery, status, scan_id=scan_id)

    log.info(
        "webhook.gitlab.processed",
        project_id=project_id_str,
        delivery_id=delivery_id,
        event_type=event_header,
        scan_id=str(scan_id) if scan_id else None,
    )
    return WebhookProcessResult(
        status=status,
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
