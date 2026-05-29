"""
Celery application bootstrap.

Phase 0 PR #2 stood up the worker process. Phase 2 PR #8 registers the real
scan tasks (``scan_source``, ``scan_container``), the DT health/resync/orphan
tasks, and the corresponding Beat schedule.

CLAUDE.md core rule #11: environment variables are read inside the factory at
process startup, not cached as module-level constants. The Beat schedule
itself is built from constants — changing the cadence requires a code change,
which is the right granularity for cron-shaped configuration.

Task module loading:
    Celery autodiscovers task modules listed in ``include=``. We list each
    Phase 2 task module so the worker registers them on boot. Importing here
    ensures the task names are bound to ``celery_app`` (not a different
    Celery() instance constructed elsewhere).
"""

from __future__ import annotations

from datetime import timedelta

from celery import Celery
from celery.schedules import crontab
from celery.schedules import schedule as _schedule

from core.config import redis_url
from core.logging import configure_logging

# Tasks defined in this PR — listed by import path so Celery can autoload
# them. Beat schedule entries below reference these by their ``name=`` kwargs.
_TASK_INCLUDES = [
    "tasks.scan_source",
    "tasks.scan_container",
    # v2.3 r1 — Go govulncheck call-graph reachability enrichment, dispatched as
    # a follow-up after a source scan succeeds (best-effort, never blocks a scan).
    "tasks.scan_reachability",
    # feat/zip-upload (security H-fix) — stale uploaded-archive retention sweep.
    "tasks.source_archive_cleaner",
    # G3.1 — preserved scan-source tarball retention sweep (latest-per-project).
    "tasks.scan_source_cleaner",
    # scan-retention — DT-style ref-keyed scan/findings retention (DB-side
    # counterpart to the disk sweepers): reclaim superseded snapshots past grace
    # + aged-excess ref-less/failed scans per keep-last/max-age.
    "tasks.scan_retention",
    # Phase 6 PR #18 — multi-channel notification fan-out (email/Slack/Teams).
    "tasks.notify",
    # Phase 6 chore PR #19 — automated backup + restore tasks.
    "tasks.backup",
    # PR-A1 (scan stability) — reclaim workspaces left by cancelled / killed /
    # crashed scans whose `finally: rmtree` did not run.
    "tasks.workspace_cleaner",
    # W6-#42 — automatic vulnerability re-matching against preserved SBOMs.
    # Promotes DT's "rematch on DB update" feature to a Trivy-backed beat
    # after ADR-0001 removed DT.
    "tasks.vulnerability_rematch",
    # W10-D — one-shot catalog backfill for B2-001 / B2-002 (legacy DT-era
    # rows with summary == details and markdown-scalar references). Not on
    # the Beat schedule; triggered manually by the operator. See the module
    # docstring for the invocation.
    "tasks.vulnerability_catalog_refresh",
    # W6-#44 — Trivy DB weekly refresh beat. Pairs with the worker-boot
    # bootstrap hook (tasks.trivy_db_bootstrap) so a fresh worker picks up
    # the DB once at start, and a running deployment refreshes weekly to
    # keep the vulnerability feed within ~Trivy's upstream cadence.
    "tasks.trivy_db_refresh",
    # W6-#44 — worker-boot bootstrap hook. NOT a Celery task — this module
    # registers a ``worker_ready`` signal handler that fires
    # ``trivy --download-db-only`` on a background thread once the worker
    # is consuming the queue. Listed here so the worker process actually
    # imports it (otherwise the signal handler never registers).
    "tasks.trivy_db_bootstrap",
]


def _build_beat_schedule() -> dict[str, dict[str, object]]:
    """
    Return the Celery Beat schedule.

    Periodic tasks the worker / beat pair fires (post-W6 — DT beats removed
    per ADR-0001 and replaced by the W6-#42 vulnerability rematch entry):
      - ``trustedoss.source_archive_cleaner``       — every 6 hours
      - ``trustedoss.scan_source_cleaner``          — every 6 hours
      - ``trustedoss.workspace_cleaner``            — every 30 minutes
      - ``trustedoss.backup.run``                   — daily at 00:00 UTC
      - ``trustedoss.vulnerability_rematch_enqueue`` — every 6h at :15
      - ``trustedoss.trivy_db_refresh``             — weekly, Sun 03:00 UTC

    chore PR #4 wires a `celery-beat` sidecar in
    ``docker-compose.dev.yml`` so these schedules actually fire.
    """
    return {
        # feat/zip-upload (security H-fix) — reclaim abandoned / orphaned
        # uploaded archives every 6h so a looped-upload DoS or a
        # SIGKILL-before-extract leak cannot fill the workspace volume.
        "source-archive-cleaner-six-hourly": {
            "task": "trustedoss.source_archive_cleaner",
            "schedule": _schedule(timedelta(hours=6)),
        },
        # G3.1 — reclaim superseded preserved-source tarballs every 6h. Retention
        # is latest-succeeded-per-project; a new succeeded scan supersedes the
        # prior tarball, and this sweep deletes everything but the retained one
        # (plus any referenced by a non-terminal scan).
        "scan-source-cleaner-six-hourly": {
            "task": "trustedoss.scan_source_cleaner",
            "schedule": _schedule(timedelta(hours=6)),
        },
        # scan-retention — reclaim DB scan rows every 6h (DT-style). ``minute=30``
        # offsets this from the :00 6h sweepers (source_archive / scan_source) and
        # the :15 rematch beat so the four 6h beats fan out across the hour rather
        # than colliding on one tick. Superseded snapshots past grace are deleted
        # (cascade reclaims findings); ref-less/failed excess is trimmed by
        # keep-last/max-age. Live ref snapshots + releases + latest are protected.
        "scan-retention-six-hourly": {
            "task": "trustedoss.scan_retention",
            "schedule": crontab(minute=30, hour="*/6"),
        },
        # PR-A1 (scan stability) — reclaim orphaned scan workspaces every
        # 30 minutes. Cheap (one stat() per dir + a single bounded SELECT),
        # frequent enough that a SIGKILL/cancel orphan never lingers long
        # enough to threaten the disk hard limit.
        "workspace-cleaner-half-hourly": {
            "task": "trustedoss.workspace_cleaner",
            "schedule": _schedule(timedelta(minutes=30)),
        },
        # Phase 6 chore PR #19 — daily auto-backup at 00:00 UTC. The task
        # itself applies a 7-day retention pass to ``auto-*`` backups after
        # writing the new artifact; manual backups are never auto-pruned.
        "daily-auto-backup": {
            "task": "trustedoss.backup.run",
            "schedule": crontab(hour=0, minute=0),
            "kwargs": {"kind": "auto", "actor_user_id": None},
        },
        # W6-#42 — automatic vulnerability re-matching every 6 hours. The
        # 6h cadence + the per-scan VULN_REMATCH_INTERVAL_HOURS knob (default
        # 6h) keeps a scan's findings within ~one full refresh window of
        # upstream NVD changes without re-running Trivy on every tick.
        # ``minute=15`` offsets this from the other 6h beats (source_archive,
        # scan_source — both on the :00 offset) so the worker pool sees a
        # staggered load profile, not three beats firing the same minute.
        "vulnerability-rematch-six-hourly": {
            "task": "trustedoss.vulnerability_rematch_enqueue",
            "schedule": crontab(minute=15, hour="*/6"),
        },
        # W6-#44 — weekly Trivy DB refresh. Sunday 03:00 UTC was chosen as
        # the lowest-traffic window on the typical enterprise cluster: 03:00
        # UTC is overnight in the Americas, early morning in EMEA, and
        # workday-lunch in APAC — every region's CI/CD churn is at its trough,
        # so a 1-3 minute lock contention on cache_dir/db/ during the download
        # never noticeably extends a user scan. The W6-#42 rematch beat picks
        # up the new advisories on its next 6h tick (latency from refresh to
        # operator notification: at most 6 hours + the per-scan match time).
        # Trivy's upstream rebuild cadence is ~6h, so a weekly pull is the
        # right floor on egress (≈1 manifest + delta layers per week per
        # worker) without sacrificing meaningful freshness. Operators on
        # tighter SLAs can swap to ``crontab(minute=15, hour=3)`` for daily
        # via TRIVY_DB_REFRESH_HOURS (the W6-#43e admin panel surfaces the
        # configured cadence next to the metadata.json UpdatedAt).
        "trivy-db-refresh-weekly": {
            "task": "trustedoss.trivy_db_refresh",
            "schedule": crontab(minute=0, hour=3, day_of_week="sun"),
        },
    }


def create_celery_app() -> Celery:
    broker = redis_url()
    app = Celery(
        "trustedoss",
        broker=broker,
        backend=broker,
        include=list(_TASK_INCLUDES),
    )
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        # PR-A1 (scan stability): do NOT set a GLOBAL task time limit here.
        # A global ``task_soft_time_limit`` / ``task_time_limit`` would also
        # cap notification / backup tasks, which is wrong — a 1-hour ceiling
        # on a Slack webhook is meaningless and a backup of a large DB can
        # legitimately run longer than a scan. Scan tasks instead receive
        # their limits per-dispatch in ``tasks.enqueue_scan`` (read from env
        # at call time per CLAUDE.md rule #11) so only the two scan tasks
        # are time-boxed.
        task_default_queue="trustedoss.default",
        timezone="UTC",
        enable_utc=True,
        beat_schedule=_build_beat_schedule(),
        # Use JSON serialization end-to-end. Pickle is the Celery default but
        # opens an RCE surface if the broker is ever exposed; JSON forces
        # task arguments to be plain strings/ints (we pass UUIDs as str).
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )
    configure_logging()
    return app


celery_app = create_celery_app()
