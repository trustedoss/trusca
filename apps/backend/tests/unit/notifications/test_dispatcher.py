"""
Unit tests for :mod:`notifications.dispatcher`.

The dispatcher is the integration seam every caller (Celery task, password
reset service, scan completion hook, ...) talks to. Its contract:

  - validates ``kind`` + ``channels`` up front;
  - aggregates per-channel outcomes into ``ok / skipped / failed``;
  - flags ``retryable_failures`` when at least one channel raised
    :class:`NotificationDeliveryError` so the Celery wrapper can retry;
  - never raises on permanent (4xx) failures or skip (env unset) — those
    are reported, not propagated.

We monkeypatch the per-channel ``send_*`` coroutines so each test can
choreograph the success / skip / fail mix without touching httpx or
aiosmtplib at all.
"""

from __future__ import annotations

import pytest

from notifications import (
    NotificationDeliveryError,
    NotificationDisabled,
    NotificationError,
)
from notifications import dispatcher as disp


@pytest.fixture(autouse=True)
def _patch_channels(monkeypatch):
    """Reset the per-channel sends to no-ops by default."""

    async def _noop_email(*, recipients, payload):
        return None

    async def _noop_slack(*, payload):
        return None

    async def _noop_teams(*, payload):
        return None

    monkeypatch.setattr(disp, "_send_email_channel", _noop_email)
    monkeypatch.setattr(disp, "_send_slack_channel", _noop_slack)
    monkeypatch.setattr(disp, "_send_teams_channel", _noop_teams)


async def test_dispatch_unknown_kind_is_skipped_not_raised() -> None:
    # H-5 defensive: a kind with no external builder must NOT crash the dispatch
    # (which would fail the Celery task and drop any in-app row it also writes).
    report = await disp.dispatch(
        kind="not_a_kind",
        context={},
        channels=["email", "slack"],
    )
    assert report["kind"] == "not_a_kind"
    assert report["delivered_count"] == 0
    assert report["skipped_count"] == 2
    assert report["failed_count"] == 0
    assert report["retryable_failures"] is False
    assert all(c["status"] == "skipped" for c in report["channels"])


def test_inapp_and_dispatch_catalogs_stay_in_sync() -> None:
    """H-5 drift guard: pin both catalogs and the kinds that must cross over.

    The in-app catalog (notification_kind enum / Pydantic Literal) and the
    external-dispatch catalog (dispatcher.NotificationKind) are intentionally
    distinct sets — in-app rows vs transactional sends — but they drifted to a
    single shared kind (scan_completed). Any kind a producer routes to BOTH
    surfaces must exist in both; this test fails if that invariant regresses.
    """
    from models.notification import NOTIFICATION_KIND_VALUES
    from schemas.notification import NotificationKind as InAppLiteral

    inapp = set(NOTIFICATION_KIND_VALUES)
    dispatch = {k.value for k in disp.NotificationKind}

    # Model enum and Pydantic Literal must never diverge from each other.
    literal_values = set(InAppLiteral.__args__)  # type: ignore[attr-defined]
    assert literal_values == inapp, "in-app enum and Pydantic Literal drifted"

    # Kinds that cross both surfaces (written in-app AND dispatched externally).
    crossover = {"scan_completed", "approval_state_changed"}
    assert crossover <= inapp, f"crossover kinds missing from in-app: {crossover - inapp}"
    assert crossover <= dispatch, f"crossover kinds missing from dispatch: {crossover - dispatch}"


async def test_dispatch_unknown_channel_raises_value_error() -> None:
    with pytest.raises(ValueError):
        await disp.dispatch(
            kind=disp.NotificationKind.PASSWORD_RESET,
            context={"reset_url": "x"},
            channels=["sms"],
        )


async def test_dispatch_all_ok_returns_clean_report() -> None:
    report = await disp.dispatch(
        kind=disp.NotificationKind.PASSWORD_RESET,
        context={"reset_url": "https://app/reset"},
        channels=[disp.CHANNEL_EMAIL, disp.CHANNEL_SLACK, disp.CHANNEL_TEAMS],
        recipients=["alice@example.com"],
    )

    assert report["delivered_count"] == 3
    assert report["skipped_count"] == 0
    assert report["failed_count"] == 0
    assert report["retryable_failures"] is False
    statuses = {entry["status"] for entry in report["channels"]}
    assert statuses == {"ok"}


async def test_dispatch_disabled_channel_recorded_as_skipped(monkeypatch) -> None:
    async def _slack_disabled(*, payload):
        raise NotificationDisabled("Slack not configured")

    monkeypatch.setattr(disp, "_send_slack_channel", _slack_disabled)

    report = await disp.dispatch(
        kind=disp.NotificationKind.SCAN_COMPLETED,
        context={"project_name": "p", "scan_id": "1"},
        channels=[disp.CHANNEL_SLACK, disp.CHANNEL_EMAIL],
        recipients=["a@example.com"],
    )

    assert report["delivered_count"] == 1
    assert report["skipped_count"] == 1
    assert report["failed_count"] == 0
    assert report["retryable_failures"] is False
    slack_row = next(c for c in report["channels"] if c["channel"] == "slack")
    assert slack_row["status"] == "skipped"


async def test_dispatch_retryable_failure_sets_retry_flag(monkeypatch) -> None:
    async def _slack_5xx(*, payload):
        raise NotificationDeliveryError("Slack 503")

    monkeypatch.setattr(disp, "_send_slack_channel", _slack_5xx)

    report = await disp.dispatch(
        kind=disp.NotificationKind.NEW_CRITICAL_CVE,
        context={"cve_id": "CVE-1", "project_name": "p"},
        channels=[disp.CHANNEL_SLACK],
    )

    assert report["delivered_count"] == 0
    assert report["failed_count"] == 1
    assert report["retryable_failures"] is True
    failed_row = report["channels"][0]
    assert failed_row["status"] == "failed"
    assert failed_row["retryable"] is True


async def test_dispatch_permanent_failure_does_not_set_retry_flag(monkeypatch) -> None:
    """Permanent (4xx) failures report failed but retryable_failures stays False."""

    async def _teams_4xx(*, payload):
        raise NotificationError("Teams rejected payload: 400")

    monkeypatch.setattr(disp, "_send_teams_channel", _teams_4xx)

    report = await disp.dispatch(
        kind=disp.NotificationKind.USER_DEACTIVATED,
        context={"user_email_hint": "a***@x.com"},
        channels=[disp.CHANNEL_TEAMS],
    )

    assert report["failed_count"] == 1
    assert report["retryable_failures"] is False
    failed_row = report["channels"][0]
    assert failed_row["status"] == "failed"
    assert failed_row["retryable"] is False


async def test_dispatch_partial_success_reports_each_channel(monkeypatch) -> None:
    """One ok + one skipped + one retryable failure = all three statuses present."""

    async def _slack_skip(*, payload):
        raise NotificationDisabled("not configured")

    async def _teams_fail(*, payload):
        raise NotificationDeliveryError("transient")

    monkeypatch.setattr(disp, "_send_slack_channel", _slack_skip)
    monkeypatch.setattr(disp, "_send_teams_channel", _teams_fail)

    report = await disp.dispatch(
        kind=disp.NotificationKind.APPROVAL_STATE_CHANGED,
        context={"component_label": "lodash@4.17.21", "new_state": "approved"},
        channels=[
            disp.CHANNEL_EMAIL,
            disp.CHANNEL_SLACK,
            disp.CHANNEL_TEAMS,
        ],
        recipients=["a@example.com"],
    )

    assert report["delivered_count"] == 1
    assert report["skipped_count"] == 1
    assert report["failed_count"] == 1
    assert report["retryable_failures"] is True
    statuses = {entry["channel"]: entry["status"] for entry in report["channels"]}
    assert statuses["email"] == "ok"
    assert statuses["slack"] == "skipped"
    assert statuses["teams"] == "failed"


async def test_dispatch_email_with_no_recipients_is_skipped(monkeypatch) -> None:
    """Email channel without recipients reports skipped, not failed.

    This guards the password-reset path where the email lookup may yield
    an empty list (e.g. unmatched email) — we still want "skipped", not
    a phantom retry.
    """

    # Override the autouse no-op email so we exercise the real "no recipients"
    # path: the dispatcher should translate the raised NotificationDisabled
    # into a skipped channel.
    async def _real_no_recipients(*, recipients, payload):
        if not recipients:
            raise NotificationDisabled("email channel requested with no recipients")

    monkeypatch.setattr(disp, "_send_email_channel", _real_no_recipients)

    report = await disp.dispatch(
        kind=disp.NotificationKind.PASSWORD_RESET,
        context={"reset_url": "https://x"},
        channels=[disp.CHANNEL_EMAIL],
        recipients=[],
    )

    assert report["delivered_count"] == 0
    assert report["skipped_count"] == 1
    assert report["failed_count"] == 0
    assert report["retryable_failures"] is False


async def test_dispatch_kind_can_be_str_or_enum() -> None:
    """The Celery task forwards the JSON string; the password-reset service
    forwards the enum. Both must work."""
    report_enum = await disp.dispatch(
        kind=disp.NotificationKind.PASSWORD_RESET,
        context={"reset_url": "x"},
        channels=[disp.CHANNEL_EMAIL],
        recipients=["a@example.com"],
    )
    report_str = await disp.dispatch(
        kind="password_reset",
        context={"reset_url": "x"},
        channels=[disp.CHANNEL_EMAIL],
        recipients=["a@example.com"],
    )
    assert report_enum["kind"] == report_str["kind"] == "password_reset"
