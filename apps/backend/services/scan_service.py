# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Scan domain services — Phase 2 PR #7 (skeleton).

PR #7 only persists the `scans` row with status='queued' and `celery_task_id
= None`. The Celery `.delay(...)` call that turns the queued row into a
running pipeline lands in PR #8 — the comment
inside `trigger_scan` flags the exact insertion point.

Concurrency contract (CLAUDE.md core rule #3 + models/scan.py partial unique
index `ix_scans_project_active`): at most one scan per (project, branch) may
be in state queued|running, where all ref-less ad-hoc scans of a project count
as one branch. Two branches write disjoint snapshots, so they run in parallel;
re-triggering the same branch still conflicts. The DB rejects the second
INSERT with IntegrityError; we translate that to `ScanInProgressConflict` (409)
so callers get a stable RFC 7807 envelope instead of a Python traceback.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from core.audit import bind_audit_team as _bind_audit_team
from core.audit import get_audit_context
from core.pii_mask import mask_pii
from core.security import CurrentUser
from models import (
    AuditLog,
    LicenseFinding,
    Project,
    Scan,
    ScanArtifact,
    ScanComponent,
    VulnerabilityFinding,
)
from schemas.scan import ScanCreate
from services.source_archive_service import (
    SourceArchiveError,
    resolve_existing_archive,
)
from tasks import enqueue_scan

log = structlog.get_logger("scan.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


# feat/demo-sandbox-scan — the single project name a public-demo visitor may
# write to when the sandbox carve-out (DEMO_ALLOW_SANDBOX_SCANS) is enabled.
# Shared with scripts/seed_demo.py, which seeds the project under this exact
# name, so the write-guard (H-2) and the seed can never disagree (hardening
# rule 2: the same vocabulary living in 2+ places demands a shared constant +
# a contract test — see tests/unit/test_demo_sandbox_guard.py).
DEMO_SANDBOX_PROJECT_NAME = "Demo Sandbox"


class ScanError(Exception):
    """Base class for scan-domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Scan Error"
    # Machine-readable RFC 7807 extension fields. Class-level default is the
    # shared empty mapping; subclasses that distinguish sub-reasons set a
    # per-instance dict in __init__ (e.g. ScanDeleteConflict — M-30).
    extensions: dict[str, object] = {}


class ScanNotFound(ScanError):
    status_code = 404
    title = "Scan Not Found"


class ScanForbidden(ScanError):
    status_code = 403
    title = "Forbidden"


class DemoSandboxScanKindNotAllowed(ScanError):
    """422 — a non-source scan kind was requested against the public demo sandbox.

    feat/demo-sandbox-scan (security review finding). The public demo opts into
    live scans via ``DEMO_ALLOW_SANDBOX_SCANS``, but the middleware carve-out
    gates on method + path only — a ``kind:"container"`` in the request body
    would otherwise ride the ``/scans`` path. Container scans pull the
    attacker-supplied ``image_ref`` through ``trivy image <ref>`` with NO
    SSRF / IP-pin guard (unlike the git_url source path, which goes through
    ``validate_git_url_with_ip``), so a public visitor could reach internal or
    cloud link-local metadata endpoints (169.254.169.254, …). The sandbox is
    therefore confined to source scans; any non-source kind is rejected here.
    """

    status_code = 422
    title = "Scan Kind Not Allowed In Demo Sandbox"


class ScanInProgressConflict(ScanError):
    status_code = 409
    title = "Scan Already In Progress"

    def __init__(self, detail: str, *, active_scan_id: uuid.UUID | None = None) -> None:
        """Carry the id of the scan that is holding the slot, when we know it.

        A CI client that hits this has nothing useful to do with a bare 409: it
        cannot start a scan, and re-running only hits the same wall. What it
        actually wants is the scan already covering this ref, so it can wait on
        that one instead of failing the build. The common trigger is a workflow
        cancelling its own job mid-scan — the runner dies, the server-side scan
        keeps going, and the replacement run collides with it.

        ``None`` when the racing scan reached a terminal state between the
        insert and this lookup, which is not worth a retry loop: the caller can
        simply trigger again.
        """
        super().__init__(detail)
        self.active_scan_id = active_scan_id
        self.extensions = (
            {"active_scan_id": str(active_scan_id)} if active_scan_id is not None else {}
        )


async def _active_scan_id_for_ref(
    session: AsyncSession,
    project_id: uuid.UUID,
    ref: str | None,
) -> uuid.UUID | None:
    """Find the queued/running scan occupying ``(project_id, ref)``.

    Called only after the partial unique index has already rejected an insert,
    so a row is expected — but it may have finished in between, hence the
    nullable return rather than an assertion.

    ``IS NOT DISTINCT FROM`` mirrors the index's NULLS NOT DISTINCT semantics:
    ref-less ad-hoc scans collide with each other, and ``Scan.ref == None``
    would evaluate to NULL and match nothing.
    """
    stmt = (
        select(Scan.id)
        .where(Scan.project_id == project_id)
        .where(Scan.ref.is_not_distinct_from(ref))
        .where(Scan.status.in_(("queued", "running")))
        .order_by(Scan.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _in_progress_detail(project_id: object, ref: str | None) -> str:
    """Name the branch that is busy, not just the project.

    Since the concurrency gate became per-(project, ref), "a scan is already
    running for this project" would send the caller looking for a conflict that
    may be on a branch they are not touching. The ref-less case keeps the old
    wording because there is no branch to name.
    """
    if ref:
        return f"a scan is already queued or running for {ref} in project {project_id}"
    return f"a scan is already queued or running for project {project_id}"


class ScanArchivedConflict(ScanError):
    """409 — the project is archived; archiving disables new scans (H-7).

    Archiving retires a project: it hides it from default lists AND must stop
    new work. The trigger path previously checked only team access and the
    concurrency cap, so an archived project still accepted scans and kept
    consuming worker capacity.
    """

    status_code = 409
    title = "Project Archived"


class ScanDeleteConflict(ScanError):
    """409 — the scan cannot be deleted in its current state.

    Two cases: (a) the scan is still active (queued/running) — cancel it first;
    (b) the scan carries an explicit ``metadata.release`` label and the caller
    did not pass ``force=true``. Release-labelled snapshots are immutable by
    default so a routine cleanup never silently destroys a tagged release.

    M-30: the two cases are distinguished by a machine-readable RFC 7807
    extension (``scan_active`` / ``scan_release_protected``) so automation does
    not have to parse the human ``detail`` string. Exactly one is ``true``.
    """

    status_code = 409
    title = "Scan Cannot Be Deleted"

    def __init__(
        self,
        message: str,
        *,
        scan_active: bool = False,
        scan_release_protected: bool = False,
    ) -> None:
        super().__init__(message)
        # Per-instance extensions so the two delete-conflict reasons surface as
        # distinct top-level snake_case Problem-Details fields.
        self.extensions = {
            "scan_active": scan_active,
            "scan_release_protected": scan_release_protected,
        }


class ConcurrentScanLimitExceeded(ScanError):
    """The triggering team already has the max number of active scans.

    B1: a per-team stability cap on concurrent (queued+running) scans,
    independent of the per-project active-scan unique index. Protects the
    shared Celery worker pool from a single team's burst when hundreds of
    users are online. Mapped to 429 Too Many Requests with a ``Retry-After``
    header and the RFC 7807 extension field ``limit`` so callers (and CI
    automation) can back off intelligently.

    M1 (security review): the live ``running_scans`` count is carried on the
    exception instance for server-side logging only — it is deliberately NOT
    exposed in the response body. Returning the team's real-time active-scan
    count to every team developer is an intra-team side-channel (it leaks how
    busy teammates are / how close the team is to its cap on each individual
    request). ``limit`` + ``Retry-After`` are sufficient for a client to back
    off; the precise count adds no client value over those two.

    S7 (concurrency-scaling-plan-2026-08-22.md §3.2/§4): ``estimated_wait_seconds``
    is a DIFFERENT field from ``running_scans`` and is deliberately allowed to
    reach the response body. M1's ban is on the count that produces the wait
    (how many scans this team has running right now), because that number is
    an intra-team side-channel; a rounded ETA derived from the SYSTEM-WIDE
    scan-queue backlog carries none of that team-specific information - two
    different teams hitting their own cap at the same moment see the same
    estimate. ``None`` when the estimate cannot be produced (queue-backlog
    metrics off, or the broker is unreachable - see
    :func:`_estimate_scan_queue_wait_seconds`), in which case the body simply
    omits the field; ``Retry-After`` on its own is still a valid instruction.
    """

    status_code = 429
    title = "Concurrent Scan Limit Exceeded"
    type_uri = "urn:trustedoss:problem:concurrent_scan_limit"
    # Seconds the client should wait before retrying; scans are long-running
    # so a coarse 30s back-off is appropriate (a finished scan frees a slot).
    retry_after_seconds = 30

    def __init__(
        self,
        message: str,
        *,
        running_scans: int,
        limit: int,
        estimated_wait_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        # Server-side only (log context). Not serialized into the 429 body.
        self.running_scans = running_scans
        self.limit = limit
        # S7: safe to serialize into the 429 body - see the class docstring
        # for why this is not the same leak M1 blocked.
        self.estimated_wait_seconds = estimated_wait_seconds


class ScanEnqueueFailed(ScanError):
    """The Celery dispatcher rejected the scan (broker down, bad kind, etc.).

    The Scan row has been written and then transitioned to ``status='failed'``
    with ``error_message='enqueue_failed: ...'``. The router maps this to
    503 Service Unavailable so caller automation knows it is safe to retry.
    """

    status_code = 503
    title = "Scan Enqueue Failed"


class ScanDiskFull(ScanError):
    """The host workspace volume is over the hard limit (DISK_HARD_LIMIT_PCT).

    Mapped to 503 Service Unavailable so CI integrations know to retry later.
    """

    status_code = 503
    title = "Workspace Disk Full"


class ProjectMissingForScan(ScanError):
    """The project referenced by a scan trigger no longer exists."""

    status_code = 404
    title = "Project Not Found"


class ScanArchiveMissing(ScanError):
    """An upload-source scan referenced an archive_id with no file on disk.

    Maps to 404 so CI / UI callers learn the zip must be (re-)uploaded via
    POST /v1/projects/{id}/source-archive before retriggering the scan.
    """

    status_code = 404
    title = "Source Archive Not Found"


class ScanSourceUnavailable(ScanError):
    """A source scan has no resolvable source — no git_url and no uploaded zip.

    BUG-008 silent-empty-success guard. A ``kind='source'`` scan resolves its
    tree two ways: ``metadata.source_type='upload'`` (extract an uploaded
    archive) or the default git path (clone ``project.git_url``). When BOTH are
    absent the worker's ``_fetch_source`` falls through to a legacy
    no-git_url placeholder and hands cdxgen an EMPTY workspace — the scan then
    reaches ``succeeded`` with 0 components, i.e. a *silent failure* the user
    reads as "no risks found". We reject the trigger up front with 422 so the
    caller learns it must attach a git_url or upload an archive first, instead
    of getting a misleading green scan.
    """

    status_code = 422
    title = "Source Scan Has No Source"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_access_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return team_id in actor.team_ids


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise ProjectMissingForScan(f"project {project_id} not found")
    return project


def _enforce_demo_sandbox_scan_kind(kind: str) -> None:
    """feat/demo-sandbox-scan (H-1): confine the sandbox to source scans only.

    No-op unless the public-demo carve-out (``demo_allow_sandbox_scans()``) is
    on. When it is, any ``kind`` other than ``"source"`` (container / future
    kinds) is rejected with 422 — container's ``image_ref`` has no SSRF guard.
    Read at call time (CLAUDE.md core rule #11).
    """
    from core.config import demo_allow_sandbox_scans

    if demo_allow_sandbox_scans() and kind != "source":
        raise DemoSandboxScanKindNotAllowed(
            "container and SBOM scan kinds are disabled in the public demo "
            "sandbox; only source scans (≤10 MiB) are available",
        )


def _enforce_demo_sandbox_project(project: Project) -> None:
    """feat/demo-sandbox-scan (H-2): confine writes to the seeded sandbox project.

    No-op unless the public-demo carve-out (``demo_allow_sandbox_scans()``) is
    on. When it is, the target project MUST be the seeded ``Demo Sandbox`` or
    the request is 403'd. Without this, the middleware carve-out (which matches
    ``/v1/projects/{id}/...`` for ANY id) would let a demo visitor whose seeded
    account is a Backend-team member scan / ingest into that team's OTHER
    projects, breaking the read-only invariant. Applies to BOTH the scan-trigger
    and SBOM-ingest paths (both call ``prepare_scan_target``). Read at call time
    (CLAUDE.md core rule #11).
    """
    from core.config import demo_allow_sandbox_scans

    if demo_allow_sandbox_scans() and project.name != DEMO_SANDBOX_PROJECT_NAME:
        raise ScanForbidden(
            "the public demo sandbox only accepts scans and SBOM ingest for "
            "the 'Demo Sandbox' project",
        )


def _concurrency_cap_per_team() -> int:
    """Per-team active-scan cap. Read at call time (CLAUDE.md core rule #11)."""
    from core.config import scan_concurrency_cap_per_team

    return scan_concurrency_cap_per_team()


async def _count_active_scans_for_team(
    session: AsyncSession, team_id: uuid.UUID
) -> int:
    """Count scans in state queued|running across all of the team's projects.

    B1: the per-team concurrency cap. We JOIN scans -> projects and clamp by
    Project.team_id so the count covers every project the team owns, not just
    the one being triggered. ``ix_scans_project_active`` (partial index on the
    active states) keeps the predicate cheap; ``ix_projects_team_id`` covers
    the team clamp.
    """
    stmt = (
        select(func.count())
        .select_from(Scan)
        .join(Project, Project.id == Scan.project_id)
        .where(Project.team_id == team_id)
        .where(Scan.status.in_(("queued", "running")))
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def _enforce_team_concurrency_cap(
    session: AsyncSession, team_id: uuid.UUID
) -> None:
    """Raise :class:`ConcurrentScanLimitExceeded` if the team is at the cap.

    A cap of 0 (or negative) disables the check entirely — the operator has
    opted out and only the per-(project, branch) unique index + per-user rate
    limit apply.

    Note (race window — soft cap): this SELECT-then-INSERT is not atomic
    across concurrent triggers from the same team. N requests can each read
    ``active == cap - 1`` before any of them INSERTs, and all N proceed,
    overshooting the cap.

    M2 (security review): worst-case bound. The overshoot is bounded, not
    unbounded, by two independent controls:

      * the unique partial index (``ix_scans_project_active``) guarantees at
        most ONE active scan per (project, branch), so a project contributes at
        most one per branch it is being pushed to rather than an unbounded
        number of re-triggers; and
      * the per-user scan-trigger rate limit (``SCAN_TRIGGER_RATE_LIMIT``,
        default 20/min) bounds how many triggers any one member can fire in
        the race window.

    So with ``cap`` and ``n_members`` members each able to fire at their
    per-user rate limit ``rate_limit``, the active-scan count for a team is
    bounded by::

        cap + (rate_limit * n_members) - 1

    i.e. a brief, bounded burst rather than a runaway. That is acceptable for
    a *stability* guard — the worker pool tolerates a transient overshoot, and
    finished scans free slots within minutes. We deliberately do NOT take a
    team-level advisory lock (``pg_advisory_xact_lock``): it would add a
    round-trip on the hot trigger path for a guard whose only failure mode is
    a short, bounded overshoot. The boundary + the bounded-race behaviour are
    pinned by the unit tests (incl. the high fan-out race) so a future
    tightening is a conscious change.
    """
    cap = _concurrency_cap_per_team()
    if cap <= 0:
        return
    active = await _count_active_scans_for_team(session, team_id)
    if active >= cap:
        log.warning(
            "scan.concurrency_cap_blocked",
            team_id=str(team_id),
            active_scans=active,
            limit=cap,
        )
        raise ConcurrentScanLimitExceeded(
            f"team {team_id} has {active} active scans (limit {cap})",
            running_scans=active,
            limit=cap,
            estimated_wait_seconds=await _estimate_scan_queue_wait_seconds(),
        )


async def _estimate_scan_queue_wait_seconds() -> int | None:
    """Best-effort ETA for a caller just blocked by the team concurrency cap.

    S7 (concurrency-scaling-plan-2026-08-22.md §1.1/§3.2/§4): the plan's own
    wait-prediction formula is ``floor((j-1)/S) x M`` for the j-th of N
    simultaneous arrivals against S slots averaging M seconds each. A caller
    turned away by the cap is, in effect, the next arrival behind whatever is
    already sitting in the scan queue, so ``j - 1`` is that queue's current
    depth (the backlog) and the formula collapses to
    ``floor(backlog / S) x M``.

    Returns ``None`` - never 0 - when the estimate cannot be produced:

      * ``queue_backlog_metrics_enabled()`` (M2) is off. This function reuses
        M2's own broker reader (``services.metrics_service._broker_queue_backlogs``)
        rather than opening a second connection to the broker on every 429, so
        a deployment that has not opted into that round trip for ``/metrics``
        does not pay it here either.
      * the broker cannot be reached. ``_broker_queue_backlogs`` already
        degrades to reporting 0 for every queue rather than raising (its own
        docstring), which this function cannot tell apart from a genuinely
        empty queue - so it asks ``queue_backlog_metrics_enabled()`` up front
        instead of trying to infer a broker outage from an all-zero reading.

    A missing estimate is not an error: the 429 body simply omits
    ``estimated_wait_seconds``, and the fixed ``Retry-After`` header (already
    present on every one of these responses) is still a valid instruction on
    its own.

    Blocking: the broker read is a synchronous Redis round trip (the same one
    ``services.metrics_service.render_metrics`` offloads for the same reason),
    so it runs in a worker thread via ``asyncio.to_thread`` rather than on
    this coroutine's event loop.
    """
    from core.config import (
        queue_backlog_metrics_enabled,
        scan_average_duration_seconds,
        scan_queue_slot_count,
    )

    if not queue_backlog_metrics_enabled():
        return None

    def _read_scan_queue_backlog() -> int:
        from services.metrics_service import _broker_queue_backlogs
        from tasks.celery_app import _SCAN_QUEUE

        return _broker_queue_backlogs().get(_SCAN_QUEUE, 0)

    backlog = await asyncio.to_thread(_read_scan_queue_backlog)
    slots = scan_queue_slot_count()
    average_seconds = scan_average_duration_seconds()
    return (backlog // slots) * average_seconds


def _disk_hard_limit_pct() -> float:
    """Hard cutoff for workspace disk usage. Above this, new scans 503.

    Read at call time (CLAUDE.md core rule #11). Parsing, range clamping, and
    the operator WARNING live in ``core.config.disk_hard_limit_pct`` next to
    the other env accessors, so a typo can no longer raise out of the guard.
    """
    from core.config import disk_hard_limit_pct

    return disk_hard_limit_pct()


def _check_disk_guard() -> None:
    """Raise :class:`ScanDiskFull` if workspace volume is past the hard limit.

    Blocking: ``os.statvfs`` is a syscall. This is the body that runs in a
    worker thread; everything on the event loop calls :func:`check_disk_guard`.

    Best-effort: if statvfs fails (eg. workspace dir missing), we let the
    scan through — the alternative (blanket 503) is worse than a scan that
    hits a real disk error inside the worker. Operators get the warning
    via the admin disk dashboard either way.
    """
    workspace = os.getenv("WORKSPACE_HOST_PATH", "/opt/trustedoss/workspace")
    try:
        stat = os.statvfs(workspace)
    except OSError as exc:
        log.warning(
            "scan.disk_guard_unavailable",
            workspace=workspace,
            error=type(exc).__name__,
        )
        return
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    if total <= 0:
        return
    used_pct = ((total - free) / total) * 100.0
    limit = _disk_hard_limit_pct()
    if used_pct >= limit:
        log.error(
            "scan.disk_guard_blocked",
            workspace=workspace,
            used_pct=round(used_pct, 1),
            limit_pct=limit,
        )
        raise ScanDiskFull(
            f"workspace disk usage {used_pct:.1f}% >= hard limit {limit:.1f}%"
        )


async def check_disk_guard() -> None:
    """Run the disk guard off the event loop. Raises :class:`ScanDiskFull`.

    ``os.statvfs`` returns in microseconds on a local filesystem, but the
    workspace is an operator-chosen path and may be an NFS or other network
    mount. A syscall against an unresponsive mount does not time out on any
    schedule we control, and on the event loop it takes every other request in
    the process down with it: including the health endpoint that would show
    what is wrong. The webhook receiver made this worth moving: the guard used
    to run only on API-triggered scans and now runs on every delivery that
    would enqueue one.

    A hung thread still leaks a thread, but the loop keeps serving.
    """
    await asyncio.to_thread(_check_disk_guard)


# ---------------------------------------------------------------------------
# Ref normalization (scan-retention)
# ---------------------------------------------------------------------------

_REF_PULL_RE = re.compile(r"^refs/pull/(\d+)/")
_REF_MERGE_RE = re.compile(r"^refs/merge-requests/(\d+)/")
_REF_HEADS = "refs/heads/"
_REF_TAGS = "refs/tags/"
# git check-ref-format-style allow-list applied to the normalized remainder:
# letters/digits and the limited punctuation a branch/tag name legitimately
# uses. Anything else (spaces, wildcards `*?[]`, `~^:@\\`, etc.) is rejected so
# the key never carries traversal/wildcard/log-injection content.
_REF_ALLOWED_RE = re.compile(r"^[A-Za-z0-9._/+-]+$")


def _has_control_byte(s: str) -> bool:
    """True if *s* contains any C0 (incl. NUL), DEL, or C1 control byte.

    The ref becomes a durable DB key AND a structlog / audit-CSV field, so a
    control byte (newline = log-forging, DEL/C1 = CSV-display corruption) has no
    legitimate place in it. ``ord(ch) < 0x20`` alone misses DEL (0x7F) and the
    C1 range (0x80–0x9F), so check all three.
    """
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F for ch in s)


def normalize_ref(raw: str | None) -> str | None:
    """Normalize a git ref into a stable retention key.

    DT-style ref-keyed retention groups a project's scans by the target they
    were run against, so a branch's repeated scans supersede one another. The
    webhook path carries full refs (``refs/heads/main``, ``refs/pull/12/merge``)
    while CI actions pass either ``$GITHUB_REF`` (same shape) or a bare branch
    name (``$CI_COMMIT_REF_NAME`` = ``main``). Both must converge on one value::

        refs/heads/main            -> main
        refs/tags/v1.2.3           -> v1.2.3
        refs/pull/12/merge         -> pr-12
        refs/merge-requests/7/head -> mr-7
        main                       -> main   (already bare)

    Returns ``None`` for missing / blank / oversized / control-char / traversal /
    disallowed-charset input so a junk ref never mints a phantom retention key
    (which would let a scan evade ref-keyed retire and fall through to the
    keep-last/max-age sweep, and could inject control bytes into logs/audit).
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or len(s) > 255:
        return None
    if _has_control_byte(s):
        return None

    pull = _REF_PULL_RE.match(s)
    if pull:
        return f"pr-{pull.group(1)}"
    merge = _REF_MERGE_RE.match(s)
    if merge:
        return f"mr-{merge.group(1)}"

    if s.startswith(_REF_HEADS):
        s = s[len(_REF_HEADS) :]
    elif s.startswith(_REF_TAGS):
        s = s[len(_REF_TAGS) :]
    s = s.strip("/")
    if not s:
        return None
    # git check-ref-format-style: no `..` (path traversal / git-forbidden), and
    # the whole remainder must match the allow-list. Reject otherwise so the
    # scan cleanly falls into the ad-hoc cohort instead of minting a junk key.
    if ".." in s or not _REF_ALLOWED_RE.match(s):
        return None
    return s


# ---------------------------------------------------------------------------
# Shared pre-flight guards (trigger_scan + sbom-ingest)
# ---------------------------------------------------------------------------


async def capacity_guard_reason(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
) -> str | None:
    """Name the capacity guard that would reject a scan, or None if clear.

    The same two stability guards :func:`prepare_scan_target` and
    :func:`trigger_scan` enforce — the per-team concurrent-scan cap and the
    workspace disk limit — expressed as a value rather than an exception, for
    callers that must not raise.

    The webhook receiver is that caller. It creates scan rows directly (it has
    no actor to authorize, so it cannot go through ``prepare_scan_target``),
    and it had therefore been bypassing both guards entirely: a batch push of
    many branches enqueued one scan per ref with nothing counting them, and a
    workspace over its hard limit kept accepting work. Raising there would be
    wrong too — a 4xx tells the Git host to retry a delivery that is not going
    to succeed any sooner — so it needs the verdict, not the exception.

    Returns the ``skipped_*`` status the receiver reports, which is why the
    strings live here next to the guards rather than in the caller.
    """
    try:
        await _enforce_team_concurrency_cap(session, team_id)
    except ConcurrentScanLimitExceeded:
        return "skipped_team_at_capacity"
    try:
        await check_disk_guard()
    except ScanDiskFull:
        return "skipped_disk_full"
    return None


async def prepare_scan_target(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
) -> Project:
    """Run the create-a-scan pre-flight guards and return the target project.

    Extracted verbatim from ``trigger_scan`` so the SBOM-ingest path reuses the
    SAME guard sequence (and the SAME typed exceptions / status codes) instead of
    re-implementing them. ``trigger_scan`` keeps calling this so its behaviour is
    unchanged — the only refactor is moving these lines behind a name.

    Guard order (CLAUDE.md §2 rule 1 — authz / existence ALWAYS before state):

      1. existence + team access — :class:`ProjectMissingForScan` (404) for an
         unknown id; :class:`ScanForbidden` (403) for a project in another team.
      2. project-scoped API-key boundary — a single-project CI key targeting a
         different project raises :class:`ScanForbidden` (403). (M-2)
      3. archived project — :class:`ScanArchivedConflict` (409). (H-7)
      4. per-team concurrency cap — :class:`ConcurrentScanLimitExceeded` (429).

    The 409 archived state deliberately sits AFTER the 403/404 authz checks so a
    non-member can never distinguish "archived" from "does not exist".

    The workspace disk guard (:class:`ScanDiskFull`, 503) is deliberately NOT run
    here: ``trigger_scan`` runs some kind-specific guards (upload-archive resolve,
    the BUG-008 source-unavailable check) BETWEEN the concurrency cap and the disk
    guard, so each caller invokes ``_check_disk_guard()`` itself at the right point
    to keep its established ordering byte-for-byte.
    """
    project = await _load_project(session, project_id)
    if not _can_access_team(actor, project.team_id):
        raise ScanForbidden(
            f"actor is not a member of team {project.team_id}",
        )

    # feat/demo-sandbox-scan (H-2) — in public-demo sandbox mode, confine writes
    # to the seeded "Demo Sandbox" project. A no-op when the carve-out is off.
    # Placed AFTER the team-access check so it is one of the 403 authz gates
    # (both are 403; ordering among them leaks nothing) and BEFORE the 409/429
    # state checks so a non-writable target can never surface as archived/at-cap.
    _enforce_demo_sandbox_project(project)

    # M-2 — a project-scoped API key is bounded to ITS project, not the whole
    # owning team. Without this gate a single-project CI key could trigger
    # scans on every other project of the same team (least-privilege breach).
    if (
        actor.api_key_project_id is not None
        and actor.api_key_project_id != project_id
    ):
        raise ScanForbidden(
            "API key is scoped to a different project",
        )

    # H-7 — archiving disables new scans. Reject before reserving any worker
    # slot so a retired project cannot keep consuming capacity.
    if project.archived_at is not None:
        raise ScanArchivedConflict(
            f"project {project_id} is archived; unarchive it to run new scans",
        )

    # B1 — per-team concurrency cap. Reject the trigger up front when the
    # team already has the maximum number of queued+running scans, protecting
    # the shared Celery worker pool from a single team's burst.
    await _enforce_team_concurrency_cap(session, project.team_id)

    return project


# ---------------------------------------------------------------------------
# Trigger scan (skeleton — Celery enqueue lands in PR #8)
# ---------------------------------------------------------------------------


async def trigger_scan(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    payload: ScanCreate,
    actor: CurrentUser,
) -> Scan:
    """
    Insert a queued scan row for `project_id`.

    Returns the new Scan ORM row. The router converts it to ScanPublic.

    Concurrency: relies on the partial unique index `ix_scans_project_active`
    (UNIQUE on project_id WHERE status IN ('queued','running')). When a
    second scan is triggered while one is still queued or running, Postgres
    raises IntegrityError and we translate to ScanInProgressConflict.

    PR #8 wiring (this PR):
      1. Persist the ``scans`` row with ``status='queued'``.
      2. Update ``project.latest_scan_id`` so list pages reflect the most
         recent scan even while it is still queued.
      3. Call ``enqueue_scan(scan)`` (the Celery dispatcher in
         ``tasks/__init__.py``) and store the returned task id back on the
         row. If the dispatcher raises (broker down, unknown kind), we mark
         the scan ``failed`` with ``error_message='enqueue_failed: ...'``
         and raise :class:`ScanEnqueueFailed` (503).

    Concurrency: ``ix_scans_project_active`` (UNIQUE on project_id WHERE
    status IN ('queued','running')) makes step 1 atomic — a second
    concurrent caller hits :class:`ScanInProgressConflict` (409) without
    ever reaching the Celery dispatcher.
    """
    # Shared pre-flight guards (existence/access/scope/archived/concurrency cap).
    # Extracted to ``prepare_scan_target`` so the SBOM-ingest path reuses the EXACT
    # same sequence + exceptions; trigger_scan's behaviour is unchanged. The disk
    # guard is NOT in the helper — it runs below, after the source-specific guards,
    # to keep this path's established ordering byte-for-byte.
    project = await prepare_scan_target(session, project_id=project_id, actor=actor)

    # feat/demo-sandbox-scan (H-1) — confine the public-demo sandbox to source
    # scans. Runs AFTER prepare_scan_target so existence/authz (404/403) and the
    # H-2 sandbox-project 403 are decided FIRST (hardening rule 1: authz before
    # state); only then do we reject a non-source kind with 422. A no-op when the
    # carve-out flag is off, so non-demo container scans are unaffected.
    _enforce_demo_sandbox_scan_kind(payload.kind)

    # feat/zip-upload: when the scan asks for an uploaded source archive,
    # verify the file exists on disk *before* we enqueue. Otherwise the worker
    # would dequeue, fail to find the archive, and the user sees a delayed
    # failure instead of an immediate 404. The schema layer already guaranteed
    # archive_id is a non-empty string when source_type == "upload".
    # Normalize defensively: ScanCreate._validate_metadata already constrains
    # source_type to {"git","upload"}, but normalizing here (and identically in
    # the worker's _fetch_source) means the guard never silently assumes the
    # schema is the sole gatekeeper — a future schema change or a code path that
    # builds ScanCreate without validation cannot slip a "UPLOAD"/" upload "
    # variant past the comparison (security review, medium severity, defense-in-depth).
    source_type = str(payload.metadata.get("source_type", "git")).strip().lower()
    if source_type == "upload":
        archive_id = str(payload.metadata.get("archive_id", ""))
        try:
            resolve_existing_archive(project.id, archive_id)
        except SourceArchiveError as exc:
            # ArchiveNotFound (404) — surface as a 404 scan error so the caller
            # learns the archive must be (re-)uploaded.
            raise ScanArchiveMissing(str(exc)) from exc

    # BUG-008 silent-empty-success guard: a source scan with neither an upload
    # archive nor a project git_url would have the worker scan an empty
    # workspace and report `succeeded` with 0 components — a silent failure.
    # Reject it here with an actionable 422. Only `source` scans need a source
    # tree; container scans resolve their target differently and are exempt.
    if payload.kind == "source" and source_type != "upload" and not (project.git_url or "").strip():
        raise ScanSourceUnavailable(
            "source scan requires a git_url on the project or an uploaded "
            "source archive (metadata.source_type='upload'); the project has "
            "neither"
        )

    # Phase 6 PR #19 — disk guard. Reject the scan up front when the
    # workspace volume is past DISK_HARD_LIMIT_PCT so the operator does
    # not get an in-flight Celery failure.
    await check_disk_guard()

    _bind_audit_team(project.team_id)

    # Capture identifiers BEFORE the commit. After session.rollback() the
    # Project ORM row's attributes are expired; touching them in the except
    # branch would trigger a sync lazy-load on an async engine and raise
    # MissingGreenlet. Plain locals are safe.
    project_id_value = project.id
    project_team_id = project.team_id

    # Defence in depth: even though `ScanCreate._validate_metadata` already
    # bounds size + depth, we mask any nested credential-shaped keys so the
    # audit listener (core.audit) cannot accidentally persist a secret into
    # the audit log diff JSONB. The mask returns a fresh deep copy.
    safe_metadata = mask_pii(dict(payload.metadata))

    scan = Scan(
        project_id=project_id_value,
        kind=payload.kind,
        status="queued",
        progress_percent=0,
        current_step=None,
        celery_task_id=None,  # set below after enqueue_scan(...)
        requested_by_user_id=actor.id,
        scan_metadata=safe_metadata,
        # scan-retention: stamp the normalized ref at create time so the
        # ref-keyed retire query (run when this scan later succeeds) is
        # index-driven. NULL when the trigger carried no ref (ad-hoc scans).
        ref=normalize_ref(safe_metadata.get("ref")),
    )
    session.add(scan)
    # Flush so `scan.id` is populated; we need it to update
    # `project.latest_scan_id` in the same transaction.
    try:
        await session.flush()
    except IntegrityError as exc:
        # The partial unique index on (project_id, ref) WHERE status IN
        # ('queued','running') is the canonical signal. Postgres returns the
        # constraint name in the orig message; we don't switch on it because
        # the only realistic constraint that fires from this INSERT is the
        # active-scan one — projects are validated above and the FK target
        # exists.
        await session.rollback()
        raise ScanInProgressConflict(
            _in_progress_detail(project_id_value, scan.ref),
            active_scan_id=await _active_scan_id_for_ref(
                session, project_id_value, scan.ref
            ),
        ) from exc

    # I-2: keep the project.latest_scan_id pointer in sync so list pages
    # (which load `latest_scan_id` denormalized to avoid a per-row JOIN) can
    # show "in progress" badges immediately after queueing. The same FK is
    # NOT touched on terminal status transitions — the latest scan is
    # whichever was most recently triggered, regardless of outcome.
    project.latest_scan_id = scan.id

    try:
        await session.commit()
    except IntegrityError as exc:
        # A second caller racing on the partial unique index might still
        # produce IntegrityError at commit time (the flush above is the
        # primary check, but commit-time constraint validation is also
        # possible if the txn was held briefly). Translate identically.
        await session.rollback()
        raise ScanInProgressConflict(
            _in_progress_detail(project_id_value, scan.ref),
            active_scan_id=await _active_scan_id_for_ref(
                session, project_id_value, scan.ref
            ),
        ) from exc

    await session.refresh(scan)

    # ------------------------------------------------------------------
    # Celery dispatch. Sync call (Celery's .delay() is sync) — no `await`.
    # ------------------------------------------------------------------
    try:
        celery_task_id = enqueue_scan(scan)
    except Exception as exc:
        # The scan row exists in 'queued' state but no worker will ever pick
        # it up. Flip it to 'failed' with a deterministic prefix so callers
        # can distinguish enqueue failures from pipeline failures.
        log.error(
            "scan_enqueue_failed",
            scan_id=str(scan.id),
            project_id=str(project_id_value),
            error=str(exc),
            exc_info=True,
        )
        scan.status = "failed"
        scan.error_message = f"enqueue_failed: {exc}"
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            # Failure-to-mark-failed should not mask the original cause.
            await session.rollback()
        raise ScanEnqueueFailed(
            f"failed to enqueue scan for project {project_id_value}: {exc}",
        ) from exc

    scan.celery_task_id = celery_task_id
    await session.commit()
    await session.refresh(scan)

    log.info(
        "scan_queued",
        scan_id=str(scan.id),
        project_id=str(project_id_value),
        team_id=str(project_team_id),
        kind=scan.kind,
        celery_task_id=celery_task_id,
    )
    return scan


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_scan(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    actor: CurrentUser,
) -> Scan:
    """Return the scan, raising 404 / 403 as appropriate."""
    result = await session.execute(select(Scan).where(Scan.id == scan_id))
    scan = result.scalar_one_or_none()
    if scan is None:
        raise ScanNotFound(f"scan {scan_id} not found")

    project = await _load_project(session, scan.project_id)
    if not _can_access_team(actor, project.team_id):
        raise ScanForbidden(
            f"actor is not a member of team {project.team_id}",
        )
    # M-2 — same project boundary on the read side: the CI scan-action polls
    # this endpoint with the project-scoped key, which must not be able to
    # read sibling projects' scans either.
    if (
        actor.api_key_project_id is not None
        and actor.api_key_project_id != scan.project_id
    ):
        raise ScanForbidden(
            "API key is scoped to a different project",
        )
    return scan


# ---------------------------------------------------------------------------
# Delete (scan-retention, Layer 3 — manual reclaim)
# ---------------------------------------------------------------------------


def _can_admin_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """True if *actor* is super_admin or team_admin **within** *team_id*.

    Mirrors ``project_service._can_write_project``'s cross-team escalation guard
    (CWE-863): we read the actor's role in this specific team via
    ``actor.team_roles``, never the global max ``actor.role``.
    """
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return actor.team_roles.get(team_id) == "team_admin"


def _has_release_label(scan_metadata: dict[str, object] | None) -> bool:
    """True if the scan carries a non-blank string ``release`` label.

    The single source of truth for "this is a tagged release snapshot". The SQL
    side (``tasks.scan_retention._release_absent``) is kept type-aligned: it
    counts a ``release`` value as present only when its JSON type is ``string``
    and non-blank, so a non-string value (e.g. a JSON number) reads as "no
    label" in BOTH layers — never protected in one and deletable in the other.
    """
    release = (scan_metadata or {}).get("release")
    return isinstance(release, str) and bool(release.strip())


async def _scan_child_counts(
    session: AsyncSession, scan_id: uuid.UUID
) -> dict[str, int]:
    """Count the cascade children of a scan, for audit fidelity on delete.

    DB-level ON DELETE CASCADE removes these rows without them entering
    ``session.deleted``, so the audit trail would otherwise record "1 scan
    deleted" with no sense of how many findings were destroyed.
    """
    out: dict[str, int] = {}
    for label, model in (
        ("vulnerability_findings", VulnerabilityFinding),
        ("license_findings", LicenseFinding),
        ("scan_components", ScanComponent),
        ("scan_artifacts", ScanArtifact),
    ):
        result = await session.execute(
            select(func.count()).select_from(model).where(model.scan_id == scan_id)
        )
        out[label] = int(result.scalar_one())
    return out


async def delete_scan(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    actor: CurrentUser,
    force: bool = False,
) -> None:
    """Hard-delete a scan and (via cascade) its components / findings / artifacts.

    DT-style retention reclaims most stale scans automatically; this is the
    manual escape hatch. RBAC mirrors the API-key revoke pattern
    (``services.api_key_service.revoke_api_key``): a scan the actor cannot see
    is **existence-hidden** as 404 rather than 403, so team ids cannot be probed.

    Guards:
      - active scans (queued/running) raise ``ScanDeleteConflict`` — cancel first
        so the worker is not deleting a row out from under itself.
      - a scan carrying an explicit ``metadata.release`` label is immutable
        unless ``force=True`` — a routine cleanup never silently destroys a
        tagged release snapshot.
      - overriding that immutability (``force=True`` on a release-labelled scan)
        is a governance action and requires **team_admin** (or super_admin); a
        plain developer gets 403. A non-forced delete stays at developer.

    Audit: the cascade children never enter ``session.deleted``, so we emit an
    explicit ``AuditLog`` row carrying the child-row counts and delete the scan
    with a Core ``DELETE`` (DB cascade reclaims children; the
    ``superseded_by_scan_id`` self-FK and ``projects.latest_scan_id`` are
    ON DELETE SET NULL, so no pointer dangles).
    """
    scan = (
        await session.execute(select(Scan).where(Scan.id == scan_id))
    ).scalar_one_or_none()
    if scan is None:
        raise ScanNotFound(f"scan {scan_id} not found")

    project = await _load_project(session, scan.project_id)
    if not _can_access_team(actor, project.team_id):
        # Existence-hide: a scan in another team reads as "not found".
        raise ScanNotFound(f"scan {scan_id} not found")

    if scan.status in ("queued", "running"):
        raise ScanDeleteConflict(
            f"scan {scan_id} is {scan.status}; cancel it before deleting",
            scan_active=True,
        )

    has_release = _has_release_label(scan.scan_metadata)
    if has_release and not force:
        raise ScanDeleteConflict(
            f"scan {scan_id} carries a release label; pass force=true to delete it",
            scan_release_protected=True,
        )
    if has_release and force and not _can_admin_team(actor, project.team_id):
        # The actor is in-team (passed the gate above) but lacks the role to
        # override release immutability — surface 403, not the existence-hide.
        raise ScanForbidden(
            "forcing deletion of a release-labelled scan requires team_admin",
        )

    _bind_audit_team(project.team_id)

    counts = await _scan_child_counts(session, scan_id)
    ctx = get_audit_context()
    session.add(
        AuditLog(
            action="delete",
            target_table="scans",
            target_id=str(scan_id),
            actor_user_id=actor.id,
            team_id=project.team_id,
            request_id=ctx.get("request_id"),
            ip=ctx.get("ip"),
            user_agent=ctx.get("user_agent"),
            diff={
                "reason": "manual",
                "forced": bool(force),
                "ref": scan.ref,
                "release_labelled": has_release,
                "cascade_deleted": counts,
            },
        )
    )

    # Avoid dangling the denormalized pointer at a deleted row.
    if project.latest_scan_id == scan.id:
        project.latest_scan_id = None

    # Core DELETE (not session.delete) so the explicit AuditLog above is the
    # single audit record — DB cascade reclaims the children either way.
    await session.execute(delete(Scan).where(Scan.id == scan_id))
    await session.commit()

    log.warning(
        "scan_deleted",
        scan_id=str(scan_id),
        project_id=str(project.id),
        team_id=str(project.team_id),
        forced=bool(force),
        cascade_deleted=counts,
    )


async def list_scans_for_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    page: int = 1,
    size: int = 20,
    ref: str | None = None,
) -> tuple[list[Scan], int]:
    """Return (scans, total) ordered by created_at desc, paginated.

    ``ref`` narrows the history to one normalized branch. The caller passes an
    already-normalized value (``normalize_ref``) so ``refs/heads/main`` and
    ``main`` reach the same rows the scan-create path stamped. Applied to the
    count as well, so a filtered page does not report the unfiltered total.
    """
    page = max(page, 1)
    size = max(min(size, 100), 1)

    project = await _load_project(session, project_id)
    if not _can_access_team(actor, project.team_id):
        raise ScanForbidden(
            f"actor is not a member of team {project.team_id}",
        )

    count_stmt = select(func.count()).select_from(Scan).where(Scan.project_id == project_id)
    if ref is not None:
        count_stmt = count_stmt.where(Scan.ref == ref)
    total_result = await session.execute(count_stmt)
    total = int(total_result.scalar_one())

    rows_stmt = (
        select(Scan)
        .where(Scan.project_id == project_id)
        # P1 #5 — eager-load the parent project so ScanPublic.from_scan can
        # surface project_name / project_slug on each row without a per-row
        # lazy load (which would trip the asyncpg greenlet guard).
        .options(selectinload(Scan.project))
        # ix_scans_project_created_at supports this ordering directly.
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    if ref is not None:
        rows_stmt = rows_stmt.where(Scan.ref == ref)
    rows_result = await session.execute(rows_stmt)
    rows = list(rows_result.scalars().all())
    return rows, total


# ---------------------------------------------------------------------------
# Cross-project list — Step 4 (Phase 3 wrap-up)
# ---------------------------------------------------------------------------


async def list_scans_for_actor(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    status_filter: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Scan], int]:
    """
    Return (scans, total) across every project the *actor* can see.

    Scope:
      - super_admin: all scans, regardless of team.
      - everyone else: scans whose project's team is in ``actor.team_ids``.
        An actor with no team memberships sees an empty page (not 403); the
        endpoint is read-only and "I am authenticated but my account has no
        teams yet" is a legitimate visible state for the SPA.

    ``status_filter`` is an optional value from ``SCAN_STATUS_VALUES``
    (queued/running/succeeded/failed/cancelled). Validation lives in the
    router (Pydantic regex constraint); we trust it here. Anything else is
    silently ignored — defense in depth without 422 churn.
    """
    page = max(page, 1)
    size = max(min(size, 100), 1)

    is_super = actor.is_superuser or actor.role == "super_admin"

    # Build the base query. We JOIN on Project so the WHERE clause can clamp
    # by team_id. ix_scans_project_created_at + ix_projects_team_id keep the
    # plan cheap for typical actor team-list sizes (≤ 50 teams).
    base = select(Scan).join(Project, Project.id == Scan.project_id)
    count_base = select(func.count()).select_from(Scan).join(
        Project, Project.id == Scan.project_id
    )

    if not is_super:
        team_ids = list(actor.team_ids)
        if not team_ids:
            return [], 0
        base = base.where(Project.team_id.in_(team_ids))
        count_base = count_base.where(Project.team_id.in_(team_ids))

    if status_filter is not None:
        base = base.where(Scan.status == status_filter)
        count_base = count_base.where(Scan.status == status_filter)

    total_result = await session.execute(count_base)
    total = int(total_result.scalar_one())

    # Order by created_at DESC (most recent first). Tie-break on id so
    # pagination is stable when two scans share a microsecond.
    # P1 #5 — eager-load the parent project so ScanPublic.from_scan surfaces
    # project_name / project_slug without per-row round-trips. The query
    # already JOINs Project (for the team gate) so selectinload here is a
    # second batched IN(...) — still cheap, no N+1.
    rows_stmt = (
        base.options(selectinload(Scan.project))
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    rows_result = await session.execute(rows_stmt)
    rows = list(rows_result.scalars().all())
    return rows, total


# ---------------------------------------------------------------------------
# System-triggered scans (no actor: webhooks, scheduled scans)
#
# The concurrency cap and disk guard above are written against the
# request-time AsyncSession; the scheduled-scan poller (tasks.scan_scheduler)
# runs inside a synchronous Celery worker (core.db.sync_session_scope) that
# cannot share that engine. Rather than a poller that queues Celery tasks
# straight past these guards, each has a sync twin below built from the exact
# same query/threshold, so both callers refuse a scan for the same reasons.
# ---------------------------------------------------------------------------


def capacity_guard_reason_sync(session: Session, *, team_id: uuid.UUID) -> str | None:
    """Sync twin of :func:`capacity_guard_reason`, for the Celery scheduler.

    Same two stability guards, same statement shapes; only the execution
    (``Session.execute`` vs. ``await AsyncSession.execute``) differs.
    """
    stmt = (
        select(func.count())
        .select_from(Scan)
        .join(Project, Project.id == Scan.project_id)
        .where(Project.team_id == team_id)
        .where(Scan.status.in_(("queued", "running")))
    )
    active = int(session.execute(stmt).scalar_one())
    cap = _concurrency_cap_per_team()
    if cap > 0 and active >= cap:
        return "skipped_team_at_capacity"
    if _disk_over_hard_limit():
        return "skipped_disk_full"
    return None


def _disk_over_hard_limit() -> bool:
    """Sync twin of :func:`check_disk_guard`'s predicate, without raising.

    ``_check_disk_guard`` already runs synchronously (``check_disk_guard``
    only wraps it in ``asyncio.to_thread`` for the async request path); a
    Celery worker is already off the event loop, so it is called directly.
    """
    try:
        _check_disk_guard()
    except ScanDiskFull:
        return True
    return False


async def enqueue_system_triggered_scan_async(
    session: AsyncSession,
    project: Project,
    *,
    metadata: dict[str, object],
) -> uuid.UUID | None:
    """Create a queued ``kind='source'`` Scan with no human actor, and dispatch it.

    The shared "no actor" path: originally the webhook receiver's private
    helper, promoted here so any async caller (webhooks today) reuses ONE
    guard-and-insert sequence rather than each re-implementing it. Returns the
    new scan id, or ``None`` if a scan is already in progress for this project
    (``ix_scans_project_active`` makes that an idempotent no-op: a scan is
    already queued, no need to add another).
    """
    _bind_audit_team(project.team_id)

    # Read the id BEFORE any statement that may roll back. A rollback expires
    # every ORM object in the session, so a later ``project.id`` triggers a
    # synchronous lazy reload outside the greenlet context and raises
    # MissingGreenlet, a 500 instead of the skip this function performs.
    project_id_str = str(project.id)

    scan = Scan(
        project_id=project.id,
        kind="source",
        status="queued",
        progress_percent=0,
        current_step=None,
        celery_task_id=None,
        requested_by_user_id=None,  # system-triggered, no user actor
        scan_metadata=metadata,
        ref=normalize_ref(metadata.get("ref")),  # type: ignore[arg-type]
    )
    session.add(scan)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        log.info("system_scan.skip_in_progress", project_id=project_id_str)
        return None

    project.latest_scan_id = scan.id
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        log.info("system_scan.skip_in_progress_commit", project_id=project_id_str)
        return None

    await session.refresh(scan)

    try:
        celery_task_id = enqueue_scan(scan)
        scan.celery_task_id = celery_task_id
        await session.commit()
        await session.refresh(scan)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "system_scan.enqueue_failed",
            scan_id=str(scan.id),
            project_id=str(project.id),
            error=str(exc),
            exc_info=True,
        )
        scan.status = "failed"
        scan.error_message = f"system_enqueue_failed: {exc}"
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()

    return scan.id


def enqueue_system_triggered_scan_sync(
    session: Session,
    project: Project,
    *,
    metadata: dict[str, object],
) -> uuid.UUID | None:
    """Sync twin of :func:`enqueue_system_triggered_scan_async`, for Celery.

    Byte-for-byte the same guard/insert sequence; the scheduled-scan poller
    (tasks.scan_scheduler) is the only caller, and it runs inside
    ``core.db.sync_session_scope`` (CLAUDE.md: no asyncpg engine inside a
    Celery worker). Kept in this module rather than local to the task so
    "the existing scan service path" names one place, not two.
    """
    _bind_audit_team(project.team_id)
    project_id_str = str(project.id)

    scan = Scan(
        project_id=project.id,
        kind="source",
        status="queued",
        progress_percent=0,
        current_step=None,
        celery_task_id=None,
        requested_by_user_id=None,
        scan_metadata=metadata,
        ref=normalize_ref(metadata.get("ref")),  # type: ignore[arg-type]
    )
    session.add(scan)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        log.info("system_scan.skip_in_progress", project_id=project_id_str)
        return None

    project.latest_scan_id = scan.id
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        log.info("system_scan.skip_in_progress_commit", project_id=project_id_str)
        return None

    session.refresh(scan)

    try:
        celery_task_id = enqueue_scan(scan)
        scan.celery_task_id = celery_task_id
        session.commit()
        session.refresh(scan)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "system_scan.enqueue_failed",
            scan_id=str(scan.id),
            project_id=str(project.id),
            error=str(exc),
            exc_info=True,
        )
        scan.status = "failed"
        scan.error_message = f"system_enqueue_failed: {exc}"
        try:
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()

    return scan.id


__all__ = [
    "DEMO_SANDBOX_PROJECT_NAME",
    "ConcurrentScanLimitExceeded",
    "DemoSandboxScanKindNotAllowed",
    "ProjectMissingForScan",
    "ScanArchiveMissing",
    "ScanDeleteConflict",
    "ScanEnqueueFailed",
    "ScanError",
    "ScanForbidden",
    "ScanInProgressConflict",
    "ScanNotFound",
    "capacity_guard_reason",
    "capacity_guard_reason_sync",
    "check_disk_guard",
    "delete_scan",
    "enqueue_system_triggered_scan_async",
    "enqueue_system_triggered_scan_sync",
    "get_scan",
    "list_scans_for_actor",
    "list_scans_for_project",
    "normalize_ref",
    "prepare_scan_target",
    "trigger_scan",
]
