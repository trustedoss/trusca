# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Malicious-package catalog refresh — weekly Celery beat (#26, MAL-2b).

Structural mirror of :mod:`tasks.eol_catalog_refresh`: two halves with
different network postures.

  1. **Fetch half** (env-gated, default OFF — ``MALICIOUS_REFRESH_ENABLED``):
     rebuilds the snapshot from the OSV bulk archives and writes the file the
     evaluator loads. Unlike EOL, the dataset is not stored in the status row
     — 12.8 MB does not belong in JSONB read on every panel load.
  2. **Re-stamp half** (ALWAYS runs — pure local, no egress): re-evaluates
     every catalog row against the snapshot in effect.

The re-stamp half is the reason this beat exists, and its value is the
opposite of EOL's. There, re-stamping mostly clears stale marks. Here it
mostly *finds* things: advisories are published for packages that have been
in production for months, and nobody re-scans an old release. Without this
pass a malicious dependency waits for the next build that happens to touch
it. So a clear → flagged transition raises a notification; the per-scan hook
cannot, because by definition no scan is running.

The reverse transition (flagged → clear) is normal and silent-ish — it means
the advisory was withdrawn upstream and the waiver path worked as intended —
but it is logged, because a single tick clearing many rows is worth seeing.

Failure isolation follows the KEV/EOL line: never raises into the beat,
changed-value guards make re-runs no-ops, and the status row is written on
every exit path.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import malicious_enabled, malicious_refresh_enabled
from models import (
    ComponentVersion,
    MaliciousSyncState,
    Membership,
    Project,
    Scan,
    ScanComponent,
)
from services.malicious import malicious_catalog
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.malicious_catalog_refresh")

#: Refuse a fetched snapshot smaller than this fraction of the one in place.
#: Same reasoning as the builder's floor: a partial fetch must not displace a
#: good snapshot, and "fewer malicious packages than last week" is not a thing
#: that happens on its own.
_SANITY_FLOOR = 0.5

#: Cap on notifications per tick. A snapshot that suddenly flags hundreds of
#: rows is either a real incident or a data problem; either way, mailing every
#: team member once per row helps nobody. Mirrors the rematch beat's cap.
_MAX_NOTIFICATIONS = 50


def _sync_state_values(summary: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Column values for the status row UPSERT."""
    values: dict[str, Any] = {
        "id": True,
        "last_result": "skipped" if summary["skipped"] else "synced",
        "skipped_reason": summary["skipped_reason"],
        "stamped": summary["stamped"],
        "flagged": summary["flagged"],
        "newly_flagged": summary["newly_flagged"],
        "duration_ms": int(summary["duration_seconds"] * 1000),
        "updated_at": now,
    }
    if summary["snapshot_date"]:
        values["snapshot_date"] = date.fromisoformat(summary["snapshot_date"])
    if summary["purl_count"] is not None:
        values["purl_count"] = summary["purl_count"]
    # Fetch-only fields stay untouched on a skipped tick so the panel keeps
    # showing when the last real sync happened.
    if not summary["skipped"]:
        values["last_synced_at"] = now
        values["ecosystems_ok"] = summary["ecosystems_ok"]
        values["ecosystems_failed"] = summary["ecosystems_failed"]
    return values


def _persist_sync_state(summary: dict[str, Any]) -> None:
    """UPSERT the single ``malicious_sync_state`` row."""
    from core.db import sync_session_scope

    values = _sync_state_values(summary, datetime.now(tz=UTC))
    stmt = (
        pg_insert(MaliciousSyncState)
        .values(values)
        .on_conflict_do_update(
            index_elements=[MaliciousSyncState.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
    )
    with sync_session_scope() as session:
        session.execute(stmt)
        session.commit()


def _fetch_half(summary: dict[str, Any]) -> None:
    """Run the env-gated snapshot rebuild. Records its outcome in *summary*.

    Off by default: this is a new egress target, and the repo's convention is
    that a new one opts in explicitly. An air-gapped install leaves it alone
    and runs on the snapshot shipped with the release — which is the whole
    reason that snapshot is in the repo.
    """
    if not malicious_refresh_enabled():
        summary["skipped_reason"] = "refresh_disabled"
        return

    previous = malicious_catalog.load_index()
    previous_count = len(previous.packages) if previous else 0

    try:
        from scripts.refresh_malicious_snapshot import main as rebuild

        code = rebuild()
    except Exception as exc:  # noqa: BLE001 — a fetch failure is a degradation
        summary["skipped_reason"] = f"unexpected:{type(exc).__name__}"
        log.warning("malicious_refresh_failed", error=str(exc), exc_info=True)
        return

    if code != 0:
        # The builder writes nothing on failure and says why on stderr; the
        # previous snapshot stays in place.
        summary["skipped_reason"] = "feed_unavailable"
        return

    malicious_catalog.load_index.cache_clear()
    refreshed = malicious_catalog.load_index()
    if refreshed is None:
        summary["skipped_reason"] = "feed_unavailable"
        return

    if previous_count and len(refreshed.packages) < previous_count * _SANITY_FLOOR:
        # The builder has its own floor; this one guards the case where it
        # wrote a file that nonetheless collapsed relative to what we ran on.
        summary["skipped_reason"] = "feed_below_sanity_floor"
        log.warning(
            "malicious_refresh_below_floor",
            previous=previous_count,
            fetched=len(refreshed.packages),
        )
        return

    summary["skipped"] = False
    summary["ecosystems_ok"] = len(refreshed.ecosystems)
    summary["ecosystems_failed"] = 0


def _affected_projects(
    session: Any, purls: list[str]
) -> dict[str, list[tuple[uuid.UUID, str, uuid.UUID]]]:
    """purl → the (project_id, project_name, team_id) rows that carry it.

    Only the latest succeeded scan of each project counts. A package that was
    removed three releases ago is not something to page anyone about, and the
    same resolver decides what the inventory and the gate consider "in use".
    """
    if not purls:
        return {}

    from services.scan_resolution import latest_succeeded_scan_select

    current = latest_succeeded_scan_select(Project.archived_at.is_(None))
    rows = session.execute(
        select(
            ComponentVersion.purl_with_version,
            Project.id,
            Project.name,
            Project.team_id,
        )
        .select_from(ScanComponent)
        .join(
            ComponentVersion,
            ComponentVersion.id == ScanComponent.component_version_id,
        )
        .join(Scan, Scan.id == ScanComponent.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .where(ComponentVersion.purl_with_version.in_(purls))
        .where(Scan.id.in_(current))
        .distinct()
    ).all()

    out: dict[str, list[tuple[uuid.UUID, str, uuid.UUID]]] = {}
    for purl, project_id, project_name, team_id in rows:
        if team_id is None:
            continue
        out.setdefault(purl, []).append((project_id, project_name, team_id))
    return out


def _notify_newly_flagged(
    session: Any, newly: list[tuple[str, str | None]]
) -> int:
    """One notification per (project × team member) for each newly flagged purl.

    Late import and per-descriptor swallow mirror the SLA sweep: a broker
    hiccup must not undo the re-stamp pass that already wrote its verdicts.
    """
    if not newly:
        return 0

    by_purl = _affected_projects(session, [purl for purl, _ in newly])
    if not by_purl:
        # Flagged rows nobody currently ships. The verdict is still written;
        # there is simply no team to tell.
        return 0

    from tasks.notify import send_notification_task

    enqueued = 0
    capped = False
    for purl, advisory_id in newly:
        for project_id, project_name, team_id in by_purl.get(purl, []):
            if enqueued >= _MAX_NOTIFICATIONS:
                capped = True
                break
            title = f"Known-malicious package in {project_name}"
            body = (
                f"{purl} is listed as malicious by a newly published advisory"
                f"{f' ({advisory_id})' if advisory_id else ''}. Remove it and "
                "rotate the credentials this build could reach — upgrading "
                "does not help."
            )
            for user_id in _team_member_user_ids(session, team_id):
                try:
                    send_notification_task.delay(
                        "malicious_detected",
                        {
                            "project_name": project_name,
                            "component_purl": purl,
                            "advisory_id": advisory_id or "",
                        },
                        [],  # channels — in-app only, as with vuln_sla_breach
                        [],  # recipients
                        user_id=str(user_id),
                        in_app_title=title,
                        in_app_body=body,
                        in_app_link=(
                            f"/projects/{project_id}"
                            "?tab=components&malicious=true"
                        ),
                        in_app_target_table="projects",
                        in_app_target_id=str(project_id),
                    )
                    enqueued += 1
                except Exception:  # noqa: BLE001 — per-descriptor
                    log.warning(
                        "malicious_notify_failed", purl=purl, exc_info=True
                    )
        if capped:
            break

    if capped:
        # A snapshot that flags this much at once is an incident or a data
        # problem; either way one row per person is not the way to say so.
        log.warning(
            "malicious_notify_capped",
            newly_flagged=len(newly),
            cap=_MAX_NOTIFICATIONS,
        )
    return enqueued


def _team_member_user_ids(session: Any, team_id: uuid.UUID) -> list[uuid.UUID]:
    """Every membership of the owning team.

    Not admin-only: removing the package and rotating credentials is work the
    developers do. Per-user muting happens downstream in the notify task.
    """
    rows = session.execute(
        select(Membership.user_id).where(Membership.team_id == team_id)
    ).all()
    return [r[0] for r in rows]


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.malicious_catalog_refresh",
    bind=True,
    # No autoretry — the weekly cadence absorbs transient failures.
    max_retries=0,
)
def refresh_malicious_catalog(self: Any) -> dict[str, Any]:
    """Fetch (optional) + re-stamp the catalog's malicious columns.

    Returns a summary dict and never raises into the beat.
    """
    started = time.monotonic()
    summary: dict[str, Any] = {
        "skipped": True,
        "skipped_reason": None,
        "ecosystems_ok": 0,
        "ecosystems_failed": 0,
        "snapshot_date": None,
        "purl_count": None,
        "stamped": 0,
        "flagged": 0,
        "newly_flagged": 0,
        "notifications_enqueued": 0,
        "duration_seconds": 0.0,
    }

    if not malicious_enabled():
        summary["skipped_reason"] = "disabled"
        summary["duration_seconds"] = time.monotonic() - started
        _persist_sync_state(summary)
        return summary

    try:
        _fetch_half(summary)

        evaluator = malicious_catalog.build_evaluator()
        index = malicious_catalog.load_index()
        if evaluator is None or index is None:
            summary["skipped_reason"] = summary["skipped_reason"] or "feed_unavailable"
            return summary

        summary["snapshot_date"] = index.snapshot
        summary["purl_count"] = len(index.packages)

        from core.db import sync_session_scope

        newly: list[tuple[str, str | None]] = []
        now = datetime.now(tz=UTC)
        with sync_session_scope() as session:
            rows = session.execute(select(ComponentVersion)).scalars().all()
            for row in rows:
                was_flagged = row.malicious_state == "flagged"
                verdict = evaluator.verdict_for(
                    row.purl_with_version,
                    malicious_catalog.version_from_purl(row.purl_with_version)
                    or row.version,
                )
                if malicious_catalog.stamp_component_version(row, verdict, now):
                    summary["stamped"] += 1
                    if not was_flagged and verdict.state == "flagged":
                        newly.append((row.purl_with_version, verdict.advisory_id))
                    elif was_flagged and verdict.state == "clear":
                        log.info(
                            "malicious_flag_lifted",
                            purl=row.purl_with_version,
                            source=verdict.source,
                        )
            session.commit()

            summary["flagged"] = int(
                session.execute(
                    select(func.count()).where(
                        ComponentVersion.malicious_state == "flagged"
                    )
                ).scalar_one()
            )

            summary["newly_flagged"] = len(newly)
            summary["notifications_enqueued"] = _notify_newly_flagged(session, newly)
    except Exception as exc:  # noqa: BLE001 — a beat must not raise
        summary["skipped_reason"] = f"unexpected:{type(exc).__name__}"
        log.warning("malicious_refresh_tick_failed", error=str(exc), exc_info=True)
    finally:
        summary["duration_seconds"] = time.monotonic() - started
        try:
            _persist_sync_state(summary)
        except Exception:  # noqa: BLE001 — status write is best-effort
            log.warning("malicious_sync_state_write_failed", exc_info=True)

    log.info(
        "malicious_catalog_refresh_complete",
        skipped=summary["skipped"],
        skipped_reason=summary["skipped_reason"],
        stamped=summary["stamped"],
        flagged=summary["flagged"],
        newly_flagged=summary["newly_flagged"],
        notifications_enqueued=summary["notifications_enqueued"],
        duration_seconds=round(summary["duration_seconds"], 2),
    )
    return summary


__all__ = ["refresh_malicious_catalog"]
