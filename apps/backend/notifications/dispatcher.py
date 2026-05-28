"""
Notification dispatcher — Phase 6 PR #18.

Single entry point that the rest of the backend uses to send a notification.
Callers pass:

  - ``kind``       — one of :class:`NotificationKind` (drives the message
                     builder lookup and the audit-log breadcrumb).
  - ``context``    — a dict of variables consumed by the kind-specific
                     builder (e.g. ``user_email``, ``reset_url``,
                     ``cve_id``, ``project_name``). The dispatcher does NOT
                     interpret these; it forwards them to the builder so
                     each kind can declare its own contract.
  - ``channels``   — list of channel names to deliver to (subset of
                     ``email`` / ``slack`` / ``teams``). The dispatcher
                     iterates the list in order; if a channel raises
                     :class:`NotificationDisabled` the dispatcher records
                     ``status='skipped'`` and moves on without retrying.
  - ``recipients`` — list of email recipients (only consulted by the
                     ``email`` channel). Slack / Teams use the configured
                     webhook URL.

Returns a structured per-channel report::

    {
        "kind": "password_reset",
        "channels": [
            {"channel": "email",  "status": "ok"},
            {"channel": "slack",  "status": "skipped",
             "reason": "Slack not configured"},
        ],
        "delivered_count": 1,
        "skipped_count": 1,
        "failed_count": 0,
        "retryable_failures": False,
    }

The Celery task wrapper (:mod:`tasks.notify`) inspects
``retryable_failures`` to decide whether to bubble
:class:`NotificationDeliveryError` back to Celery's autoretry envelope. If
some channels delivered and only some are transient-failed we still raise
so the task is retried — Celery does not have a partial-success branch and
re-running a successful channel is acceptable for the kinds we currently
support (each is idempotent at the recipient end: another email, another
Slack message — annoying, not dangerous).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

import structlog

from . import NotificationDeliveryError, NotificationDisabled, NotificationError
from .email import send_email
from .slack import send_slack
from .teams import send_teams

log = structlog.get_logger("notifications.dispatcher")


# ---------------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------------


class NotificationKind(str, Enum):
    """Closed set of notification kinds the portal currently emits.

    Adding a kind requires:
      1. A new entry here.
      2. A builder in :data:`_BUILDERS` that turns ``context`` into the
         per-channel payload tuple.
      3. (Optional) a frontend i18n string for the receipt-side rendering.
    """

    NEW_CRITICAL_CVE = "new_critical_cve"
    SCAN_COMPLETED = "scan_completed"
    APPROVAL_STATE_CHANGED = "approval_state_changed"
    USER_DEACTIVATED = "user_deactivated"
    PASSWORD_RESET = "password_reset"  # noqa: S105 — kind name, not a credential


# Channel name strings used in the public API.
CHANNEL_EMAIL = "email"
CHANNEL_SLACK = "slack"
CHANNEL_TEAMS = "teams"
_KNOWN_CHANNELS = frozenset({CHANNEL_EMAIL, CHANNEL_SLACK, CHANNEL_TEAMS})


# ---------------------------------------------------------------------------
# Per-channel payload builders
# ---------------------------------------------------------------------------


def _ctx_str(context: dict[str, Any], key: str, default: str = "") -> str:
    """Read ``context[key]`` defensively — always return a string.

    Builders should not crash on missing keys; the audit trail captures the
    full ``context`` so the operator can debug a misshapen call upstream.
    """
    value = context.get(key, default)
    if value is None:
        return default
    return str(value)


def _build_password_reset(
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return ``(email_kwargs, slack_kwargs, teams_kwargs)`` for password reset."""
    reset_url = _ctx_str(context, "reset_url")
    expires_minutes = _ctx_str(context, "expires_minutes", "60")
    user_email_hint = _ctx_str(context, "user_email_hint", "your account")

    subject = "TrustedOSS Portal — Reset your password"
    body_text = (
        f"A password reset was requested for {user_email_hint}.\n\n"
        f"Open the link below within {expires_minutes} minutes to set a new password:\n\n"
        f"  {reset_url}\n\n"
        "If you did not request this, you can safely ignore this message.\n"
    )
    body_html = (
        "<p>A password reset was requested for <strong>"
        f"{user_email_hint}</strong>.</p>"
        f"<p>Open the link below within {expires_minutes} minutes to set a new"
        " password:</p>"
        f"<p><a href=\"{reset_url}\">{reset_url}</a></p>"
        "<p>If you did not request this, you can safely ignore this message.</p>"
    )

    return (
        {"subject": subject, "body_text": body_text, "body_html": body_html},
        {"text": "A password reset link was issued for a TrustedOSS Portal user."},
        {
            "title": "Password reset requested",
            "text": "A password reset link was issued for a TrustedOSS Portal user.",
        },
    )


def _build_new_critical_cve(
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    cve_id = _ctx_str(context, "cve_id", "<unknown>")
    project_name = _ctx_str(context, "project_name", "<unknown project>")
    severity = _ctx_str(context, "severity", "CRITICAL")
    summary = (
        f"[{severity}] {cve_id} found in project {project_name}."
    )
    return (
        {
            "subject": f"TrustedOSS — {severity} CVE in {project_name}",
            "body_text": (
                f"{summary}\n\nReview the project's Vulnerabilities tab for"
                " details and triage."
            ),
        },
        {"text": summary},
        {"title": f"{severity} CVE detected", "text": summary},
    )


def _build_scan_completed(
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    project_name = _ctx_str(context, "project_name", "<unknown project>")
    scan_id = _ctx_str(context, "scan_id")
    status = _ctx_str(context, "status", "completed")
    summary = f"Scan {scan_id} for project {project_name} {status}."
    return (
        {
            "subject": f"TrustedOSS — Scan {status}: {project_name}",
            "body_text": summary,
        },
        {"text": summary},
        {"title": f"Scan {status}", "text": summary},
    )


def _build_approval_state_changed(
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    component = _ctx_str(context, "component_label", "<component>")
    new_state = _ctx_str(context, "new_state", "updated")
    summary = f"Component approval changed: {component} → {new_state}."
    return (
        {
            "subject": f"TrustedOSS — Approval {new_state}: {component}",
            "body_text": summary,
        },
        {"text": summary},
        {"title": f"Approval {new_state}", "text": summary},
    )


def _build_user_deactivated(
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    user_email_hint = _ctx_str(context, "user_email_hint", "<user>")
    summary = f"Account deactivated: {user_email_hint}."
    return (
        {
            "subject": "TrustedOSS — Account deactivated",
            "body_text": summary,
        },
        {"text": summary},
        {"title": "Account deactivated", "text": summary},
    )


# Lookup keyed by the ``str`` value of NotificationKind so the Celery task —
# which accepts the kind as a JSON string per CLAUDE.md (no pickle) — can
# resolve it without importing the enum.
_BUilderResult = tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
_BUILDERS: dict[str, Callable[[dict[str, Any]], _BUilderResult]] = {
    NotificationKind.PASSWORD_RESET.value: _build_password_reset,
    NotificationKind.NEW_CRITICAL_CVE.value: _build_new_critical_cve,
    NotificationKind.SCAN_COMPLETED.value: _build_scan_completed,
    NotificationKind.APPROVAL_STATE_CHANGED.value: _build_approval_state_changed,
    NotificationKind.USER_DEACTIVATED.value: _build_user_deactivated,
}


# ---------------------------------------------------------------------------
# Per-channel send wrappers
#
# Each wrapper returns ``None`` on success and raises one of the domain
# exceptions in ``notifications.__init__``. The dispatcher's job is to
# classify the outcome into ``ok / skipped / failed`` and aggregate.
# ---------------------------------------------------------------------------


async def _send_email_channel(
    *,
    recipients: list[str],
    payload: dict[str, Any],
) -> None:
    if not recipients:
        # Nothing to do — treat as a "disabled" branch so the per-channel
        # report says "skipped, no recipients" rather than "ok".
        raise NotificationDisabled("email channel requested with no recipients")
    await send_email(
        to=recipients,
        subject=payload["subject"],
        body_text=payload["body_text"],
        body_html=payload.get("body_html"),
    )


async def _send_slack_channel(*, payload: dict[str, Any]) -> None:
    await send_slack(text=payload["text"], blocks=payload.get("blocks"))


async def _send_teams_channel(*, payload: dict[str, Any]) -> None:
    await send_teams(title=payload["title"], text=payload["text"])


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def dispatch(
    *,
    kind: NotificationKind | str,
    context: dict[str, Any],
    channels: list[str],
    recipients: list[str] | None = None,
) -> dict[str, Any]:
    """Build per-channel payloads and deliver to each requested channel.

    Returns a structured report (see module docstring). Raises
    :class:`NotificationDeliveryError` ONLY when at least one channel
    suffered a transient failure — Celery uses this as the retry signal.

    The dispatcher does NOT raise on permanent failures (4xx) or skips
    (channel not configured). Those are recorded in the report and the
    caller decides whether to surface them.
    """
    kind_str = kind.value if isinstance(kind, NotificationKind) else str(kind)
    builder = _BUILDERS.get(kind_str)
    if builder is None:
        raise ValueError(f"unknown notification kind: {kind_str!r}")

    # Validate channel set up front so a typo never silently no-ops.
    unknown = [c for c in channels if c not in _KNOWN_CHANNELS]
    if unknown:
        raise ValueError(f"unknown channels: {unknown}")

    email_payload, slack_payload, teams_payload = builder(context)
    recipient_list = list(recipients or [])

    results: list[dict[str, Any]] = []
    delivered = 0
    skipped = 0
    failed = 0
    retryable = False

    # Channel name -> coroutine factory. The factory captures the per-channel
    # payload so the dispatch loop only worries about the kind/status mapping.
    channel_runners: dict[str, Callable[[], Awaitable[None]]] = {
        CHANNEL_EMAIL: lambda: _send_email_channel(
            recipients=recipient_list, payload=email_payload
        ),
        CHANNEL_SLACK: lambda: _send_slack_channel(payload=slack_payload),
        CHANNEL_TEAMS: lambda: _send_teams_channel(payload=teams_payload),
    }

    for channel in channels:
        runner = channel_runners[channel]
        try:
            await runner()
        except NotificationDisabled as exc:
            skipped += 1
            results.append(
                {"channel": channel, "status": "skipped", "reason": str(exc)}
            )
            log.info(
                "notification_channel_skipped",
                kind=kind_str,
                channel=channel,
                reason=str(exc),
            )
        except NotificationDeliveryError as exc:
            failed += 1
            retryable = True
            results.append(
                {
                    "channel": channel,
                    "status": "failed",
                    "retryable": True,
                    "error": str(exc),
                }
            )
            log.warning(
                "notification_channel_retryable_failure",
                kind=kind_str,
                channel=channel,
                error=str(exc),
            )
        except NotificationError as exc:
            failed += 1
            results.append(
                {
                    "channel": channel,
                    "status": "failed",
                    "retryable": False,
                    "error": str(exc),
                }
            )
            log.warning(
                "notification_channel_permanent_failure",
                kind=kind_str,
                channel=channel,
                error=str(exc),
            )
        except ValueError as exc:
            # Programmer error inside the builder / channel wrapper. Treat
            # as permanent so the Celery retry envelope does not loop on it.
            failed += 1
            results.append(
                {
                    "channel": channel,
                    "status": "failed",
                    "retryable": False,
                    "error": f"value_error: {exc}",
                }
            )
            log.error(
                "notification_channel_value_error",
                kind=kind_str,
                channel=channel,
                error=str(exc),
            )
        else:
            delivered += 1
            results.append({"channel": channel, "status": "ok"})

    summary = {
        "kind": kind_str,
        "channels": results,
        "delivered_count": delivered,
        "skipped_count": skipped,
        "failed_count": failed,
        "retryable_failures": retryable,
    }
    log.info(
        "notification_dispatch_complete",
        kind=kind_str,
        delivered=delivered,
        skipped=skipped,
        failed=failed,
        retryable=retryable,
    )
    return summary


__all__ = [
    "CHANNEL_EMAIL",
    "CHANNEL_SLACK",
    "CHANNEL_TEAMS",
    "NotificationKind",
    "dispatch",
]
