"""
Notification channels — Phase 6 PR #18.

Three out-of-the-box channels:

  - ``email``  — SMTP via :mod:`aiosmtplib` (TLS / STARTTLS).
  - ``slack``  — Slack incoming webhook POST.
  - ``teams``  — MS Teams incoming webhook POST (Adaptive Card / MessageCard).

Each channel module exposes a single async ``send_*`` coroutine that either
returns ``None`` on success or raises one of the domain exceptions defined
here. The dispatcher (``notifications.dispatcher.dispatch``) sequences the
channel sends, captures partial-success outcomes, and is what Celery wraps
under ``trustedoss.send_notification`` so the retry / backoff envelope is
handled in one place.

Design notes:

  - **No module-level env reads** (CLAUDE.md core rule #11). Channel modules
    re-fetch SMTP_*/SLACK_WEBHOOK_URL/TEAMS_WEBHOOK_URL inside the call so
    the worker reflects ``docker-compose --env-file`` changes without a
    rebuild.
  - **Clear sentinel for "not configured"**: :class:`NotificationDisabled`
    is raised when the channel's required env is unset. The dispatcher
    treats it as "skip", not "fail" — a deployment with only Slack should
    not have email failures dragging the whole notification into retry.
  - **Delivery errors retry** via :class:`NotificationDeliveryError`. The
    Celery task hooks ``autoretry_for=(NotificationDeliveryError,)`` so
    transient SMTP greylisting / 5xx Slack errors are retried with
    exponential backoff (10-min cap).
  - **No PII in logs.** Channel implementations log
    recipient counts and channel names but not addresses, subjects, or
    bodies. The dispatcher passes structured ``context`` into each builder
    rather than concatenating user-supplied strings into log lines.
"""

from __future__ import annotations


class NotificationError(Exception):
    """Base class for notification errors. Mirrors the AuthError pattern."""


class NotificationDisabled(NotificationError):
    """The requested channel is not configured (env unset).

    The dispatcher treats this as a soft skip: the channel is recorded as
    ``status='skipped'`` in the per-channel result and no retry is scheduled.
    """


class NotificationDeliveryError(NotificationError):
    """Transient delivery failure — Celery should retry.

    Examples:
      - SMTP 4xx greylist response or socket error.
      - Slack / Teams webhook 5xx.
      - HTTP timeout.

    4xx responses from Slack / Teams (e.g. ``invalid_payload``) are
    permanent and should NOT raise this — they raise the parent
    :class:`NotificationError` so the dispatcher records ``status='failed'``
    without retrying.
    """


__all__ = [
    "NotificationDeliveryError",
    "NotificationDisabled",
    "NotificationError",
]
