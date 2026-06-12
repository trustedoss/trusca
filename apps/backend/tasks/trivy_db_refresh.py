"""
Trivy DB weekly refresh — Celery Beat task (W6-#44).

Re-runs ``trivy --download-db-only`` on a beat schedule (default Sunday 03:00
UTC — the lowest-traffic window on the typical enterprise cluster) so the
vulnerability DB stays current without operator intervention. The W6-#42
``vulnerability_rematch`` beat then picks up the new advisories on its own
6-hour cadence and re-runs ``trivy sbom`` against preserved SBOMs to surface
any new criticals as notifications.

Why a separate Celery task, not just a cron inside the container:
  - The Celery worker is the only process with the trivy binary AND the
    cache volume mount. A host cron would have to ``docker-compose exec``
    which adds an indirection layer + a second auth surface.
  - The beat schedule is environment-aware (UTC across all replicas) and
    its history is visible in the same admin/health panel that surfaces the
    DB freshness from W6-#43e.
  - Failure notifications (network down, mirror unreachable, disk full)
    route through the existing notification dispatcher (Slack / Teams) —
    no new alerting channel needed.

Lock contention with running scans:
  Trivy itself takes a file lock on ``cache_dir/db/`` for the duration of
  the download. A scan worker that calls ``trivy sbom`` mid-refresh will
  block on the lock (typically <60s for an incremental refresh) and then
  read the freshly-swapped manifest. This is acceptable: the user's scan
  is still progressing, just slightly delayed, and the per-scan latency
  was 10× larger when DT was the matcher. We do NOT pre-emptively pause
  scan dispatch around the beat tick — the lock provides correct
  serialisation without coordination overhead.

Idempotency:
  Trivy's download is idempotent — re-running with a current manifest is a
  no-op (it stats the on-disk manifest, compares the upstream digest, and
  exits 0). So a duplicate beat tick (e.g. a worker pod restart racing a
  scheduled fire) is cheap.

CLAUDE.md compliance:
  - Core rule #3: Trivy invocation sits behind a Celery task (this module).
  - Core rule #4: matching is Trivy-only; this task feeds the engine.
  - Core rule #11: every env knob is read inside ``download_db_only`` /
    ``trivy_db_refresh_timeout_seconds`` at call time, never cached at the
    module level.
  - §5 logging: structlog JSON, one event per line; stderr beyond the first
    1000 chars is never logged.
"""

from __future__ import annotations

from typing import Any

import structlog

from core.config import trivy_db_refresh_timeout_seconds
from integrations.trivy import (
    TrivyDbDownloadResult,
    download_db_only,
    get_trivy_db_status,
)
from notifications.dispatcher import (
    CHANNEL_SLACK,
    CHANNEL_TEAMS,
    NotificationKind,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.trivy_db_refresh")


# Status values that warrant an operator notification. ``skipped`` (mock
# backend / trivy not installed) is intentionally not in this set — those
# are dev paths and should never wake up the on-call channel.
_NOTIFIABLE_FAILURE_STATUSES: frozenset[str] = frozenset({"timeout", "failed"})


def _dispatch_failure_notification(result: TrivyDbDownloadResult) -> int:
    """Enqueue a ``trustedoss.send_notification`` for a refresh failure.

    Best-effort: a per-descriptor enqueue failure logs a WARNING but does
    not crash the task. Returns the number of descriptors actually enqueued.

    We reuse the existing ``NEW_CRITICAL_CVE`` notification kind with a
    composite ``cve_id`` value because:
      * it already lights up the Slack / Teams cards admins watch, and
      * the existing builder accepts a free-form ``cve_id`` string without
        needing a new ``NotificationKind`` enum value (which would touch
        i18n strings + the FE notification centre + the schema).
    The follow-up note in the W6-#44 PR description proposes a dedicated
    ``TRIVY_DB_REFRESH_FAILED`` kind once the FE notification surface
    grows a per-kind icon palette.
    """
    # Late import so the unit test can monkeypatch the dispatcher entry
    # before this module gets pulled in by ``tasks.celery_app``.
    from tasks.notify import send_notification_task

    title = "TRUSCA — Trivy DB refresh failed"
    detail = result.error or "unknown"
    body = (
        f"{title}\n\nstatus: {result.status}\n"
        f"duration: {result.duration_seconds:.1f}s\n"
        f"detail: {detail}\n"
    )
    if result.stderr_tail:
        body += f"\nstderr tail:\n{result.stderr_tail}\n"
    descriptor = {
        "kind": NotificationKind.NEW_CRITICAL_CVE.value,
        "context": {
            "cve_id": f"TRIVY-DB-REFRESH-{result.status.upper()}",
            "project_name": "Trivy DB lifecycle",
            "severity": "HIGH",
            # Free-form body the Slack / Teams builders ignore but the
            # email path surfaces. Existing dispatcher tolerates extra
            # context keys.
            "body": body,
        },
        "channels": [CHANNEL_SLACK, CHANNEL_TEAMS],
        "recipients": [],
    }
    try:
        send_notification_task.delay(
            descriptor["kind"],
            descriptor["context"],
            descriptor["channels"],
            descriptor["recipients"],
        )
        return 1
    except Exception as exc:  # noqa: BLE001 — broker failure must not crash the beat
        log.warning(
            "trivy_db_refresh_notification_dispatch_failed",
            error=str(exc)[:300],
        )
        return 0


@celery_app.task(name="trustedoss.trivy_db_refresh")  # type: ignore[misc]
def refresh_trivy_db() -> dict[str, Any]:
    """Beat entry — re-download the Trivy vulnerability DB.

    Returns a structured summary the admin/health panel (and the test suite)
    can assert against:
        ``{"status", "duration_seconds", "vuln_count_before",
           "vuln_count_after", "db_version_before", "db_version_after",
           "notification_enqueued"}``

    The function NEVER raises. A genuinely unexpected error (DB cache dir
    yanked mid-call, structlog exhaustion, etc.) is caught, logged, and
    surfaced in the summary with ``status="failed"``.
    """
    structlog.contextvars.bind_contextvars(task_name="trivy_db_refresh")
    summary: dict[str, Any] = {
        "status": "unknown",
        "duration_seconds": 0.0,
        "vuln_count_before": None,
        "vuln_count_after": None,
        "db_version_before": None,
        "db_version_after": None,
        "notification_enqueued": 0,
    }
    try:
        # Snapshot BEFORE so we can surface the delta in the log / admin
        # widget. A failure to read the metadata.json (corrupt / absent —
        # this is the first-ever download path) just leaves the "before"
        # fields at None.
        try:
            before = get_trivy_db_status()
            summary["vuln_count_before"] = before.vuln_count
            summary["db_version_before"] = before.db_version
        except Exception as exc:  # noqa: BLE001 — pre-snapshot is best-effort
            log.info("trivy_db_refresh_snapshot_before_failed", error=str(exc)[:300])

        timeout = trivy_db_refresh_timeout_seconds()
        result = download_db_only(timeout_seconds=timeout)
        summary["status"] = result.status
        summary["duration_seconds"] = result.duration_seconds

        # Snapshot AFTER — even a failed refresh wants the prior DB version
        # surfaced so the admin can spot "still on last week's manifest".
        try:
            after = get_trivy_db_status()
            summary["vuln_count_after"] = after.vuln_count
            summary["db_version_after"] = after.db_version
        except Exception as exc:  # noqa: BLE001 — post-snapshot is best-effort
            log.info("trivy_db_refresh_snapshot_after_failed", error=str(exc)[:300])

        if result.status in _NOTIFIABLE_FAILURE_STATUSES:
            summary["notification_enqueued"] = _dispatch_failure_notification(result)
            log.warning(
                "trivy_db_refresh_failure",
                status=result.status,
                duration_seconds=result.duration_seconds,
                error=result.error,
            )
        elif result.status == "skipped":
            # mock backend / trivy missing — common in dev, never paged.
            log.info(
                "trivy_db_refresh_skipped",
                duration_seconds=result.duration_seconds,
            )
        else:
            # downloaded → success path. Log the delta in vuln_count when
            # we have both endpoints; the panel uses this to render a
            # "+1,234 advisories since last week" microcopy.
            before_count = summary["vuln_count_before"]
            after_count = summary["vuln_count_after"]
            delta = (
                after_count - before_count
                if isinstance(before_count, int) and isinstance(after_count, int)
                else None
            )
            log.info(
                "trivy_db_refresh_complete",
                duration_seconds=result.duration_seconds,
                vuln_count_before=before_count,
                vuln_count_after=after_count,
                delta=delta,
            )
        return summary
    except Exception as exc:  # noqa: BLE001 — beat tick must not raise
        # A truly unexpected error (e.g. structlog backend gone) is caught
        # so the beat keeps progressing through subsequent ticks. We log
        # WARNING (not ERROR) because the underlying refresh path is
        # already best-effort.
        summary["status"] = "failed"
        summary["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        log.warning(
            "trivy_db_refresh_unexpected_error",
            error=str(exc)[:300],
            error_type=type(exc).__name__,
        )
        return summary
    finally:
        structlog.contextvars.unbind_contextvars("task_name")


__all__ = ["refresh_trivy_db"]
