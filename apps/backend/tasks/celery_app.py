# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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

from core.config import broker_visibility_timeout_seconds, redis_url
from core.logging import configure_logging

# Importing for the side effect: the module connects the three Celery signals
# that carry ``request_id`` from the dispatching request into the worker and
# clear it afterwards. Nothing here calls into it.
from tasks import log_context as _log_context  # noqa: F401

# Tasks defined in this PR — listed by import path so Celery can autoload
# them. Beat schedule entries below reference these by their ``name=`` kwargs.
_TASK_INCLUDES = [
    "tasks.scan_source",
    "tasks.scan_container",
    # External CycloneDX SBOM ingest — reuses the source pipeline's back half
    # (components → Trivy SBOM matching → findings → finalize) against an
    # uploaded SBOM (no clone / cdxgen / scancode / signing).
    "tasks.ingest_sbom",
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
    # D7 (N18): a fixed-interval poller that starts a scan on its own, for
    # whichever projects have a schedule (or inherit the organization
    # default). No schedule anywhere means no automatic scan.
    "tasks.scan_scheduler",
    # Phase 6 PR #18 — multi-channel notification fan-out (email/Slack/Teams).
    "tasks.notify",
    # D5 (N11): post an event worth a ticket to whatever the organisation
    # runs. Its own task so a slow tracker costs a worker slot rather than a
    # scan, which is the failure this integration usually has.
    "tasks.ticket_webhook",
    # D6 (N17): hand the audit trail to whatever the organisation collects
    # logs with, a batch at a time. Off unless a destination is configured.
    "tasks.audit_export",
    # Phase 6 chore PR #19 — automated backup + restore tasks.
    "tasks.backup",
    # PR-A1 (scan stability) — reclaim workspaces left by cancelled / killed /
    # crashed scans whose `finally: rmtree` did not run.
    "tasks.workspace_cleaner",
    # Hold the dependency-resolution caches (Maven, npm, Go, NuGet, the
    # license index) under a size cap. They are what makes the second scan of
    # a dependency fast, and until this task nothing ever deleted any of it.
    "tasks.toolchain_cache_cleaner",
    # The row-side counterpart to workspace_cleaner: a scan whose worker was
    # SIGKILLed or restarted stays `running` forever, holding both the
    # project's active-scan slot and one of the team's concurrency slots.
    "tasks.stale_scan_reaper",
    # W6-#42 — automatic vulnerability re-matching against preserved SBOMs.
    # Promotes DT's "rematch on DB update" feature to a Trivy-backed beat
    # after ADR-0001 removed DT.
    "tasks.vulnerability_rematch",
    # X1 step 2 — daily SLA-breach sweep: aggregates open findings that
    # crossed their per-severity SLA due date in the trailing 24h window and
    # fans out one in-app alert per project to the owning team's members.
    "tasks.vuln_sla_sweep",
    # W10-D — one-shot catalog backfill for B2-001 / B2-002 (legacy DT-era
    # rows with summary == details and markdown-scalar references). Not on
    # the Beat schedule; triggered manually by the operator. See the module
    # docstring for the invocation.
    "tasks.vulnerability_catalog_refresh",
    # Phase D1 — one-shot backfill for licenses.review_flag (AI review class:
    # behavioral-use / non-commercial). Reconciles pre-classifier NULL rows.
    # Not on the Beat schedule; triggered manually by the operator. See the
    # module docstring for the invocation.
    "tasks.license_review_flag_backfill",
    # CISA KEV catalog refresh — daily beat that reconciles the
    # ``vulnerabilities.kev*`` columns (migration 0034) against CISA's public
    # Known Exploited Vulnerabilities feed. Backs the Vulnerabilities tab's
    # ``sort=priority`` ranking (KEV → severity → EPSS).
    "tasks.kev_catalog_refresh",
    # Daily EPSS score sync: fills ``vulnerabilities.epss_score`` /
    # ``epss_percentile``, which the scanner does not emit and which every
    # EPSS surface (the tab column and filter, the ``sort=priority`` ranking,
    # the reports, the optional build gate) reads. Off unless
    # EPSS_REFRESH_ENABLED.
    "tasks.epss_catalog_refresh",
    # Phase M — endoflife.date refresh beat: weekly re-stamp of the
    # ``component_versions.eol_*`` columns (migration 0038) against the
    # newest snapshot (vendored or, when EOL_REFRESH_ENABLED, freshly
    # fetched). Backs the Components tab's EOL badge/filter.
    "tasks.eol_catalog_refresh",
    # #26 — weekly re-stamp of the malicious columns, plus the optional
    # snapshot rebuild. The re-stamp half is what finds a package that turned
    # malicious after the last scan touched it.
    "tasks.malicious_catalog_refresh",
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
    # Worker-boot hook — NOT a Celery task. Registers a ``worker_ready`` signal
    # handler that makes the shared workspace volume writable by the non-root
    # backend (the root worker fixes mode to 1777 once at boot) so the backend's
    # SBOM-ingest no longer 500s with PermissionError on a fresh volume. Listed
    # here so the worker process imports it and the handler registers.
    "tasks.workspace_prep",
    # S6 (concurrency-scaling-plan-2026-08-22.md §3.2/§4) - beat sweep that
    # turns a sustained Celery-queue backlog into a Slack/Teams alert. Off
    # unless both QUEUE_BACKLOG_ALERT_ENABLED and M2's
    # QUEUE_BACKLOG_METRICS_ENABLED are set (see the module docstring).
    "tasks.queue_backlog_alert",
    # W9 (concurrency-scaling-plan-2026-08-22.md §3.5/§4) - caps the tables
    # that had no retention policy: refresh_tokens + password_reset_tokens
    # (deletes rows past their own expires_at), notifications /
    # webhook_deliveries / report_downloads (deletes rows past an
    # occurrence-time age), and a read-only audit_logs purge-readiness
    # report (never deletes, see the module docstring for why).
    "tasks.auth_token_retention",
    "tasks.operational_retention",
    "tasks.audit_log_retention",
    # S7 (concurrency-scaling-plan-2026-08-22.md §3.2/§4) - bounded, backed-
    # off retry for a webhook-triggered scan turned away by the team
    # concurrency cap or the disk guard, dispatched by
    # services.webhook_service._schedule_capacity_retry the moment that
    # happens. Not a beat entry - each delivery schedules its own chain of
    # attempts via apply_async(countdown=...).
    "tasks.webhook_capacity_retry",
]

# S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4, S3 row): the two queue
# names and the four scan-pipeline task names task_routes below sends to
# ``_SCAN_QUEUE``. Every other task name falls through to
# ``task_default_queue`` (``_DEFAULT_QUEUE``) via Celery's own default-queue
# behavior for names with no routing entry, so it is not enumerated here.
#
# These are literal constants, not read from
# ``tests/contracts/queue-names.json`` at runtime, because that file lives at
# the repo root, outside ``apps/backend``. The backend and worker Docker
# images build with ``apps/backend`` as their build context
# (``.github/workflows/release.yml``), so a shipped image does not contain
# ``tests/contracts/``. The values are hand-kept equal to that file instead,
# and ``tests/unit/tasks/test_queue_routing_contract.py`` is the
# vocabulary-consistency check (repository hardening rule 2) that fails if
# this dict and the JSON file drift apart.
_SCAN_QUEUE = "trustedoss.scan"
_DEFAULT_QUEUE = "trustedoss.default"
_SCAN_TASK_NAMES = (
    "trustedoss.scan_source",
    "trustedoss.scan_container",
    "trustedoss.ingest_sbom",
    "trustedoss.scan_reachability",
)

# Beat-schedule key of the KEV catalog refresh entry. Shared with
# ``services.kev_health_service``, which derives the admin panel's
# ``next_refresh_at`` from this entry's live crontab object — a string
# literal duplicated in two modules would drift silently on a rename, so
# the key is a module constant (CLAUDE.md 표준 §2 hardening rule 2 spirit:
# one vocabulary, one owner).
KEV_BEAT_ENTRY_NAME = "kev-catalog-refresh-daily"
# Daily EPSS score sync. Same naming convention and the same reason for a
# module constant as KEV_BEAT_ENTRY_NAME above.
EPSS_BEAT_ENTRY_NAME = "epss-catalog-refresh-daily"

# Beat-schedule key of the EOL catalog refresh entry — shared with
# ``services.eol_health_service`` for the same live-crontab derivation
# (see KEV_BEAT_ENTRY_NAME's rationale above).
EOL_BEAT_ENTRY_NAME = "eol-catalog-refresh-weekly"

# Same live-crontab derivation for the malicious panel's ``next_refresh_at``.
MALICIOUS_BEAT_ENTRY_NAME = "malicious-catalog-refresh-weekly"


def _build_beat_schedule() -> dict[str, dict[str, object]]:
    """
    Return the Celery Beat schedule.

    Periodic tasks the worker / beat pair fires (post-W6 — DT beats removed
    per ADR-0001 and replaced by the W6-#42 vulnerability rematch entry):
      - ``trustedoss.source_archive_cleaner``       — every 6 hours
      - ``trustedoss.scan_source_cleaner``          — every 6 hours
      - ``trustedoss.workspace_cleaner``            — every 30 minutes
      - ``trustedoss.toolchain_cache_cleaner``      : every 6 hours, once per
        queue (each worker cleans its own cache mount)
      - ``trustedoss.stale_scan_reaper``            : every 30 minutes
      - ``trustedoss.backup.run``                   — daily at 00:00 UTC
      - ``trustedoss.vulnerability_rematch_enqueue`` — every 6h at :15
      - ``trustedoss.kev_catalog_refresh``          — daily at 01:45 UTC
      - ``trustedoss.epss_catalog_refresh``         : daily at 02:20 UTC
      - ``trustedoss.vuln_sla_sweep``               — daily at 02:45 UTC
      - ``trustedoss.malicious_catalog_refresh``    — weekly, Sun 02:40 UTC
      - ``trustedoss.trivy_db_refresh``             — weekly, Sun 03:00 UTC
      - ``trustedoss.scan_schedule_poll``           : every 15 minutes
      - ``trustedoss.queue_backlog_alert_check``    : every 5 minutes
      - ``trustedoss.auth_token_retention``         : daily at 03:15 UTC
      - ``trustedoss.operational_retention``        : daily at 03:30 UTC
      - ``trustedoss.audit_log_retention_report``   : daily at 03:45 UTC

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
        # Hold the toolchain caches under their cap, every 6 hours. Not more
        # often: the pass walks every cached artifact to size it, which is
        # six figures of small files once a corpus has been through, and the
        # thing it guards against is days of accumulation rather than a burst.
        # Same cadence as the other retention sweeps for that reason.
        #
        # ONE ENTRY PER QUEUE, and both are required. The task reads and
        # deletes files on the local filesystem of whichever worker executes
        # it, and each worker has its own cache mount (see the volume
        # declarations in docker-compose.yml). A single entry would fall
        # through to the default queue, so worker-default would tidy itself
        # while worker-scan -- which holds the larger cache, because it is
        # the one resolving dependency graphs -- grew unbounded and unwatched.
        # These are the only beat entries that pin a queue, for that reason:
        # every other task acts on the database or the shared workspace, so
        # it does not matter where it runs.
        "toolchain-cache-cleaner-default-six-hourly": {
            "task": "trustedoss.toolchain_cache_cleaner",
            "schedule": _schedule(timedelta(hours=6)),
            "options": {"queue": _DEFAULT_QUEUE},
        },
        "toolchain-cache-cleaner-scan-six-hourly": {
            "task": "trustedoss.toolchain_cache_cleaner",
            "schedule": _schedule(timedelta(hours=6)),
            "options": {"queue": _SCAN_QUEUE},
        },
        # Same cadence as the workspace cleaner it mirrors, and for the same
        # reason: one bounded SELECT, and every pass it does not run is a
        # project that keeps refusing to scan.
        "stale-scan-reaper-half-hourly": {
            "task": "trustedoss.stale_scan_reaper",
            "schedule": _schedule(timedelta(minutes=30)),
        },
        # D6 (N17): hand the audit trail to the configured collector, a batch
        # at a time. Every five minutes rather than hourly because the point
        # of a continuous export is that the collector is roughly current;
        # the task returns immediately when no destination is configured, so
        # the cost on a deployment that has not switched it on is one function
        # call reading one environment variable.
        "audit-export-every-five-minutes": {
            "task": "trustedoss.export_audit_log",
            "schedule": _schedule(timedelta(minutes=5)),
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
        # CISA KEV catalog refresh — daily at 01:45 UTC. CISA updates the
        # feed on US business days, so daily is the right cadence (weekly
        # would let a newly-listed actively-exploited CVE sit unflagged for
        # up to 6 days). ``minute=45`` keeps the minute lane clear of the
        # other beats: the 6h sweepers own :00, the rematch beat owns :15,
        # scan-retention owns :30, and the daily backup fires at 00:00 —
        # 01:45 never shares a tick with any of them (nor with the weekly
        # Trivy refresh at Sun 03:00). The work itself is tiny (one ~10 MiB
        # download + two bounded UPDATE passes), so scheduling inside the
        # 00:00 backup's hour is safe.
        KEV_BEAT_ENTRY_NAME: {
            "task": "trustedoss.kev_catalog_refresh",
            "schedule": crontab(minute=45, hour=1),
        },
        # Daily EPSS score sync at 02:20 UTC. Minute-lane check: :00 is owned
        # by the 6h sweepers, the daily backup and the weekly Trivy refresh,
        # :15 by the 6h rematch and the Sunday EOL refresh, :30 by 6h
        # scan-retention, :40 by the Sunday malicious refresh, and :45 by the
        # daily KEV refresh and the SLA sweep, so :20 is unused and this never
        # shares a tick with anything. The hour is deliberately between the
        # 01:45 KEV refresh and the 02:45 SLA sweep: both inputs to the
        # priority ranking (KEV first, then EPSS) land before the sweep reads
        # them. The work is one ~2.6 MiB download plus an UPDATE pass over
        # only the rows whose score actually moved.
        EPSS_BEAT_ENTRY_NAME: {
            "task": "trustedoss.epss_catalog_refresh",
            "schedule": crontab(minute=20, hour=2),
        },
        # X1 step 2 — daily vulnerability SLA-breach sweep at 02:45 UTC. The
        # 24h cadence pairs with the sweep's trailing-24h due-date window so
        # every breach is observed by exactly one tick (the window IS the
        # dedup — see tasks/vuln_sla_sweep's docstring). Minute-lane check:
        # :00 is owned by the 6h sweepers / daily backup / weekly Trivy,
        # :15 by the 6h rematch + Sun-02:15 EOL, :30 by 6h scan-retention,
        # and :45 by the daily KEV refresh at 01:45 — so 02:45 reuses the
        # :45 lane at a different hour and never shares a tick with any
        # other beat. The hour is deliberately AFTER the 00:15 rematch tick
        # and the 01:45 KEV refresh: the sweep then evaluates the freshest
        # finding set and severity data of the day. The work is one bounded
        # read + broker enqueues, so the overnight window is ample.
        "vuln-sla-sweep-daily": {
            "task": "trustedoss.vuln_sla_sweep",
            "schedule": crontab(minute=45, hour=2),
        },
        # Phase M — endoflife.date refresh + catalog re-stamp. Weekly is
        # proportionate to EOL churn (lifecycle dates move quarterly, not
        # daily). Sunday 02:15 UTC sits between the daily KEV tick (01:45)
        # and the weekly Trivy refresh (Sun 03:00) so no two beats share a
        # tick; the work is bounded (≤10 tiny fetches when enabled + one
        # whitelist-bounded UPDATE pass).
        EOL_BEAT_ENTRY_NAME: {
            "task": "trustedoss.eol_catalog_refresh",
            "schedule": crontab(minute=15, hour=2, day_of_week="sun"),
        },
        # #26 — Sunday 02:40 UTC. Sits between the EOL re-stamp (02:15) and
        # the Trivy DB refresh (03:00) so the three catalog passes do not
        # contend for the worker, and after EOL because this one walks every
        # component_versions row.
        MALICIOUS_BEAT_ENTRY_NAME: {
            "task": "trustedoss.malicious_catalog_refresh",
            "schedule": crontab(minute=40, hour=2, day_of_week="sun"),
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
        # D7 (N18): ONE fixed-interval poller rather than one beat entry per
        # project schedule, which would make this table grow with every
        # project that sets a cadence. 15 minutes keeps a due hour's window
        # open for four retries if the capacity/disk guard is momentarily
        # blocking, without polling so often that an idle deployment (the
        # common case: no schedule rows at all) pays for it.
        "scan-schedule-poll-15-minutes": {
            "task": "trustedoss.scan_schedule_poll",
            "schedule": _schedule(timedelta(minutes=15)),
        },
        # S6 - sample both Celery queues' broker backlog every 5 minutes and
        # alert (Slack/Teams) once one has been over its threshold for a
        # sustained window. Off unless QUEUE_BACKLOG_ALERT_ENABLED (and M2's
        # QUEUE_BACKLOG_METRICS_ENABLED) are set; the task itself is the
        # no-op when they are not (tasks.queue_backlog_alert). Five minutes
        # is deliberately shorter than the default 10-minute sustain window
        # (queue_backlog_alert_sustain_seconds()), so a breach is sampled at
        # least twice before it can alert - a single missed or delayed tick
        # cannot manufacture a page on its own.
        "queue-backlog-alert-every-five-minutes": {
            "task": "trustedoss.queue_backlog_alert_check",
            "schedule": _schedule(timedelta(minutes=5)),
        },
        # W9 - reclaim expired refresh + password-reset token rows daily.
        # Both predicates key off ``expires_at`` (already indexed on both
        # tables), so once-daily is ample against TTLs measured in days
        # (refresh) and hours (password reset). 03:15 UTC sits in the same
        # low-traffic window as the weekly Trivy refresh (03:00) without
        # sharing its tick.
        "auth-token-retention-daily": {
            "task": "trustedoss.auth_token_retention",
            "schedule": crontab(minute=15, hour=3),
        },
        # W9 - reclaim aged notifications / webhook deliveries / report
        # downloads daily. 03:30 UTC, after the auth-token sweep and before
        # the weekly Trivy refresh's own low-traffic window ends.
        "operational-retention-daily": {
            "task": "trustedoss.operational_retention",
            "schedule": crontab(minute=30, hour=3),
        },
        # W9 - audit_logs purge-readiness report. Read-only (see the module
        # docstring for why this beat never deletes). Daily is ample for an
        # operator-facing signal; 03:45 UTC completes the same maintenance
        # window as the two sweeps above.
        "audit-log-retention-report-daily": {
            "task": "trustedoss.audit_log_retention_report",
            "schedule": crontab(minute=45, hour=3),
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
        # S1 (concurrency-scaling-plan-2026-08-22.md §3.2): the Redis
        # transport's own visibility_timeout default (3600s) sits BELOW this
        # deployment's scan hard time limit default (3900s,
        # scan_hard_time_limit_seconds()). Combined with task_acks_late=True
        # above, a scan that runs past the visibility timeout is redelivered
        # to a second worker while the first worker is still running it, and
        # the same scan then occupies two slots. broker_visibility_timeout_seconds()
        # derives the timeout from the same hard-limit accessor scan dispatch
        # uses, so retuning SCAN_HARD_TIME_LIMIT_SECONDS moves this value with
        # it; it is read fresh on every create_celery_app() call (rule #11),
        # not cached as a module constant.
        broker_transport_options={
            "visibility_timeout": broker_visibility_timeout_seconds(),
        },
        # PR-A1 (scan stability): do NOT set a GLOBAL task time limit here.
        # A global ``task_soft_time_limit`` / ``task_time_limit`` would also
        # cap notification / backup tasks, which is wrong — a 1-hour ceiling
        # on a Slack webhook is meaningless and a backup of a large DB can
        # legitimately run longer than a scan. Scan tasks instead receive
        # their limits per-dispatch in ``tasks.enqueue_scan`` (read from env
        # at call time per CLAUDE.md rule #11) so only the two scan tasks
        # are time-boxed. S1's broker-level visibility_timeout above is a
        # different mechanism (redelivery, not task cancellation) and does
        # not reintroduce that global cap.
        task_default_queue=_DEFAULT_QUEUE,
        # S3: route the scan-pipeline tasks onto their own queue so a
        # 65-minute scan no longer sits in front of a one-second notification
        # or a beat sweep on the same worker line (plan §1.1's "큐가 하나다").
        # `-Q` on the worker command line (docker-compose.yml / the Helm
        # deployment-worker-{scan,default}.yaml templates, devops-owned) is
        # the other half of this split; task_routes is the half that
        # actually decides which queue a task lands on when dispatched.
        task_routes={name: {"queue": _SCAN_QUEUE} for name in _SCAN_TASK_NAMES},
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
