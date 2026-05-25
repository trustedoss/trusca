"""
Obligation catalog services — Phase 3 PR #13 (Obligations tab + NOTICE).

Three top-level entry points, each invoked from the matching router endpoint:

- :func:`list_project_obligations`
- :func:`get_obligation_detail`
- :func:`generate_notice`

Why a new module?
-----------------
``services/license_service.py`` (PR #12) is keyed by ``license_findings`` —
it answers "what licenses are present in this scan, and what are they?". The
Obligations tab asks a different question — "what duties does the project
inherit from those licenses, and how do we materialize them as a NOTICE
file?". Splitting the read into its own module mirrors PR #11's split
between ``project_detail_service`` and ``vulnerability_service``.

Read-only by design
-------------------
Obligations are a per-license policy catalog. There is no analyst workflow,
no transition matrix, no audit log. The endpoints in this module are pure
GETs — there is no PATCH counterpart. Changes to the catalog happen via
ingestion (ORT rule packs, future SPDX exception imports) or seeding, not
end-user mutation.

Authorization
-------------
All project-scoped reads existence-hide cross-team access as 404
(security-reviewer Low #4) so a non-member cannot distinguish "exists but
forbidden" from "does not exist" — uniform with the obligation-detail,
vulnerability, license, SBOM, report, and source-tree endpoints.
- List: ``ProjectNotFound`` (404) on cross-team.
- Detail: ``ObligationNotFound`` (404) on cross-team.
- Notice: ``ProjectNotFound`` (404) on cross-team.

Both the list and notice paths emit a ``log.warning("authz.cross_team_attempt",
...)`` *before* raising so SOC tooling sees the rejection regardless of which
HTTP status the caller observes.

Search safety
-------------
User-supplied ``search`` is run through :func:`core.sql_safety.escape_like`
and compared with an explicit ESCAPE clause so attackers cannot collapse
the filter to "match everything" with bare ``%`` / ``_`` characters.

Aggregation only — no denormalization
-------------------------------------
Distribution counts and ``affected_count`` are computed at query time. We do
not introduce a new ``obligation_summary`` table; the existing indexes
(``ix_license_findings_scan_id`` + ``ix_obligations_license_id``) cover the
read shapes for the latest-scan working set (db-designer verification, PR
#13).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from html import escape as html_escape
from typing import Any, cast

import structlog
from sqlalchemy import String, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from core.sql_safety import escape_like
from models import (
    Component,
    ComponentVersion,
    LicenseFinding,
    Obligation,
    Project,
)
from models import (
    License as LicenseModel,
)
from schemas.obligation_detail import KNOWN_OBLIGATION_KINDS
from services.obligation_catalog import obligations_for
from services.project_detail_service import _license_rank_case
from services.project_service import ProjectError, ProjectNotFound

log = structlog.get_logger("obligation.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ObligationError(ProjectError):
    """Base class for obligation-domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Obligation Error"


class ObligationNotFound(ObligationError):
    status_code = 404
    title = "Obligation Not Found"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALL_CATEGORY_VALUES: frozenset[str] = frozenset(
    {"allowed", "conditional", "forbidden", "unknown"}
)

_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 500
_VALID_SORT_KEYS = frozenset({"category", "license_name", "kind", "affected_count"})

# Defense-in-depth caps on the obligation drawer payload (security-reviewer
# Low #1 from PR #13). The ``affected_components`` array mirrors the
# license_service cap; the ``text`` clamp prevents a maliciously authored
# catalog row from inflating the drawer JSON beyond a sane size. Clients
# fall back to the source catalog or the Components tab when the cap fires.
_AFFECTED_COMPONENTS_CAP = 500
_OBLIGATION_TEXT_CAP_BYTES = 64 * 1024  # 64 KiB

# G2 — body-size caps on the NOTICE document (text / markdown / html). A
# pathological scan (a license attached to tens of thousands of components, or a
# runaway obligation/license text) must not produce an unbounded synchronous
# response. We keep the document legally complete for NORMAL sizes and only
# clamp the extreme tail:
#   - the per-license credited-component list is capped at
#     ``_NOTICE_COMPONENT_LABELS_CAP`` entries; the document records an honest
#     "+N more component(s) omitted" note when the cap fires so the NOTICE is
#     never silently incomplete.
#   - obligation text and license names/refs ride through ``_clamp_obligation_text``
#     (the existing 64 KiB byte clamp) so a single runaway field cannot inflate
#     the body.
# 5000 credited components per license is well past any real attribution need
# (a NOTICE lists distinct third-party packages, not files) while bounding the
# tail at a sane size.
_NOTICE_COMPONENT_LABELS_CAP = 5000

# G2 follow-up (security-reviewer Low/Info from PR #107) — the per-license
# ``component_labels`` list was already capped above, but the NUMBER of license
# sections and the NUMBER of obligations rendered PER license were unbounded. A
# pathological catalog (tens of thousands of distinct licenses, or one license
# carrying a runaway obligation set) would still inflate the synchronous NOTICE
# body section-by-section. We cap both axes and record an honest omitted-count
# in every format, mirroring the component "+N omitted" pattern, so the document
# stays legally complete for NORMAL catalogs and only clamps the extreme tail.
#
# 2000 distinct third-party licenses in a single project is already far beyond
# any real attribution surface (a NOTICE credits distinct licenses, not files),
# and 500 obligations on ONE license dwarfs every license in the seeded catalog
# — both bound the body without clipping a realistic document.
_NOTICE_LICENSE_CAP = 2000
_NOTICE_OBLIGATIONS_PER_LICENSE_CAP = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from core.authz import assert_team_access  # noqa: E402

# All cross-team guards in this module flow through `assert_team_access`
# (chore PR #3) so the `authz.cross_team_attempt` log shape is centralized.


def _normalize_category_filter(raw: list[str] | None) -> list[str] | None:
    """Drop unknown values; ``[]`` signals "no rows match", ``None`` means no filter."""
    if raw is None:
        return None
    cleaned = [c for c in raw if c in _ALL_CATEGORY_VALUES]
    if not cleaned:
        return []
    return cleaned


def _normalize_kind_filter(raw: list[str] | None) -> list[str] | None:
    """Trim + dedupe + length-cap kinds; the column is open so we accept any string."""
    if raw is None:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for k in raw:
        candidate = k.strip()
        if not candidate or len(candidate) > 64 or candidate in seen:
            continue
        seen.add(candidate)
        cleaned.append(candidate)
    if not cleaned:
        return []
    return cleaned


def _order_distribution(counts: dict[str, int]) -> dict[str, int]:
    """Order distribution dict — known kinds first, then unknown alphabetically.

    The Pydantic v2 ``dict[str, int]`` serializer preserves insertion order,
    so the API contract effectively advertises this ranking without requiring
    a list shape.
    """
    ordered: dict[str, int] = {}
    seen: set[str] = set()
    for k in KNOWN_OBLIGATION_KINDS:
        if k in counts:
            ordered[k] = counts[k]
            seen.add(k)
    for k in sorted(counts.keys()):
        if k not in seen:
            ordered[k] = counts[k]
    return ordered


# ---------------------------------------------------------------------------
# Catalog enrichment (v2.2 c4)
# ---------------------------------------------------------------------------


async def sync_catalog_obligations(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
) -> int:
    """Idempotently populate ``obligations`` from the structured catalog.

    v2.2 c4 — before this, ``obligations`` was filled ONLY by the seed scripts,
    so a real scan (which creates ``License`` rows but no ``Obligation`` rows in
    ``tasks/scan_source.py``) surfaced an empty Obligations tab and an
    obligation-free NOTICE. This function closes the gap on the READ path: for
    every license materially observed in ``scan_id`` whose SPDX id is in
    :mod:`services.obligation_catalog`, it upserts that license's concrete
    obligation rows.

    Idempotency / non-destruction (CLAUDE.md §6):
      - Uses ``INSERT ... ON CONFLICT (license_id, kind) DO NOTHING`` against the
        existing ``uq_obligations_license_kind`` constraint, so re-running is a
        no-op and any obligation a seed script / operator authored by hand for
        the SAME ``(license, kind)`` is NEVER overwritten. We only ADD missing
        rows. The ``link`` defaults to the license's ``reference_url`` so the
        drawer / NOTICE can deep-link to the canonical text.

    Scope: only licenses present in the given scan are considered, so the upsert
    cost is bounded by the project's license surface, not the whole 30-entry
    catalog × every license row in the database.

    Returns the number of obligation rows inserted (0 when nothing was missing).
    The caller is responsible for the surrounding transaction; this function
    flushes but does not commit so it composes inside a read request.
    """
    # Licenses observed in this scan, with their SPDX id + reference URL. We only
    # enrich licenses ORT actually saw — enriching the entire catalog on every
    # read would write obligations for licenses the project does not use.
    lic_stmt = (
        select(
            LicenseModel.id,
            LicenseModel.spdx_id,
            LicenseModel.reference_url,
        )
        .join(LicenseFinding, LicenseFinding.license_id == LicenseModel.id)
        .where(LicenseFinding.scan_id == scan_id)
        .where(LicenseModel.spdx_id.is_not(None))
        .distinct()
    )
    lic_rows = (await session.execute(lic_stmt)).all()
    if not lic_rows:
        return 0

    values: list[dict[str, Any]] = []
    for lic_id, spdx_id, reference_url in lic_rows:
        for kind, text, link in obligations_for(spdx_id, reference_url=reference_url):
            values.append(
                {
                    "license_id": lic_id,
                    "kind": kind,
                    "text": text,
                    "link": link,
                }
            )
    if not values:
        return 0

    # ON CONFLICT DO NOTHING on the existing (license_id, kind) unique index:
    # never clobber an operator-/seed-authored row, only fill gaps. The insert
    # is a single round-trip regardless of how many rows it carries.
    stmt = (
        pg_insert(Obligation)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_obligations_license_kind")
        .returning(Obligation.id)
    )
    result = await session.execute(stmt)
    inserted = len(result.fetchall())
    if inserted:
        await session.flush()
        log.info(
            "obligation.catalog.synced",
            scan_id=str(scan_id),
            licenses=len(lic_rows),
            inserted=inserted,
        )
    return inserted


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


async def list_project_obligations(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    limit: int = _LIST_LIMIT_DEFAULT,
    offset: int = 0,
    kinds: list[str] | None = None,
    categories: list[str] | None = None,
    search: str | None = None,
    sort: str = "category",
    order: str = "desc",
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """
    Page of obligations + per-kind distribution for the project's latest scan.

    Returns ``(items, distribution, total)``:

    - ``items``: list of plain dicts shaped to
      :class:`schemas.obligation_detail.ObligationListItem`.
    - ``distribution``: dict keyed by obligation kind (known first, unknown
      alphabetical) counting distinct (license, kind) pairs visible in the
      latest scan. Unfiltered — single source of truth for the chart.
    - ``total``: total number of distinct (license, kind) obligation rows
      after the active filter.

    Authorization
    -------------
    - ``ProjectNotFound`` (404) if the project id doesn't exist OR the actor is
      not a team member (existence-hide, security-reviewer Low #4). We log
      ``authz.cross_team_attempt`` before raising.

    If the project has no ``latest_scan_id``, returns
    ``([], {}, 0)`` with success — empty result, not 404.
    """
    if sort not in _VALID_SORT_KEYS:
        raise ObligationError(f"unsupported sort key: {sort!r}")
    if order not in {"asc", "desc"}:
        raise ObligationError(f"unsupported order: {order!r}")

    limit = max(min(int(limit), _LIST_LIMIT_MAX), 1)
    offset = max(int(offset), 0)

    project_result = await session.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_obligations",
        resource_id=str(project_id),
        # Existence-hide cross-team reads as 404 (security-reviewer Low #4):
        # uniform with the obligation-detail / vulnerability / license / SBOM /
        # report / source-tree endpoints so a non-member cannot distinguish
        # "exists but forbidden" from "does not exist".
        deny=lambda: ProjectNotFound(f"project {project_id} not found"),
    )

    if project.latest_scan_id is None:
        return [], {}, 0

    # v2.2 c4: ensure the structured obligation catalog is materialised for the
    # licenses this scan observed before we read/aggregate. Idempotent — a no-op
    # once populated, and it never overwrites seed-/operator-authored rows.
    await sync_catalog_obligations(session, scan_id=project.latest_scan_id)

    category_filter = _normalize_category_filter(categories)
    if category_filter == []:
        # Caller passed only invalid categories — match nothing without 422.
        # Distribution still reflects the underlying scan so the chart isn't
        # zeroed out behind a stale filter.
        distribution = await _compute_kind_distribution(session, project.latest_scan_id)
        return [], distribution, 0
    kind_filter = _normalize_kind_filter(kinds)
    if kind_filter == []:
        distribution = await _compute_kind_distribution(session, project.latest_scan_id)
        return [], distribution, 0

    rank = _license_rank_case()

    # Distinct-license-in-scan subquery so we only surface obligations whose
    # parent license is materially present in the latest scan. Without this
    # join we'd leak catalog rows for licenses ORT never observed.
    affected_subq = (
        select(
            LicenseFinding.license_id.label("license_id"),
            func.count(func.distinct(LicenseFinding.component_version_id)).label(
                "affected_count"
            ),
        )
        .where(LicenseFinding.scan_id == project.latest_scan_id)
        .group_by(LicenseFinding.license_id)
        .subquery()
    )

    base = (
        select(
            Obligation.id.label("id"),
            Obligation.license_id.label("license_id"),
            Obligation.kind.label("kind"),
            Obligation.text.label("text"),
            Obligation.link.label("link"),
            Obligation.updated_at.label("updated_at"),
            LicenseModel.spdx_id.label("license_spdx_id"),
            LicenseModel.name.label("license_name"),
            LicenseModel.category.label("license_category"),
            affected_subq.c.affected_count.label("affected_count"),
            rank.label("rank"),
        )
        .select_from(Obligation)
        .join(LicenseModel, LicenseModel.id == Obligation.license_id)
        .join(affected_subq, affected_subq.c.license_id == Obligation.license_id)
    )

    if category_filter:
        base = base.where(sql_cast(LicenseModel.category, String).in_(category_filter))

    if kind_filter:
        base = base.where(Obligation.kind.in_(kind_filter))

    if search:
        safe = escape_like(search.strip())
        like = f"%{safe}%"
        base = base.where(
            or_(
                LicenseModel.spdx_id.ilike(like, escape="\\"),
                LicenseModel.name.ilike(like, escape="\\"),
                Obligation.kind.ilike(like, escape="\\"),
                Obligation.text.ilike(like, escape="\\"),
            )
        )

    # Sorting — primary axis chosen by `sort`, then a deterministic tiebreak
    # so paging doesn't shuffle rows under the user.
    primary: Any
    if sort == "category":
        primary = rank.desc() if order == "desc" else rank.asc()
    elif sort == "license_name":
        primary = (
            LicenseModel.name.desc() if order == "desc" else LicenseModel.name.asc()
        )
    elif sort == "kind":
        primary = (
            Obligation.kind.desc() if order == "desc" else Obligation.kind.asc()
        )
    else:  # affected_count
        primary = (
            affected_subq.c.affected_count.desc()
            if order == "desc"
            else affected_subq.c.affected_count.asc()
        )

    order_clauses = [
        primary,
        LicenseModel.name.asc(),
        Obligation.kind.asc(),
        Obligation.id.asc(),
    ]

    items_stmt = base.order_by(*order_clauses).limit(limit).offset(offset)
    count_stmt = select(func.count()).select_from(base.subquery())

    items_result = await session.execute(items_stmt)
    rows = list(items_result.all())
    count_result = await session.execute(count_stmt)
    total = int(count_result.scalar_one())

    distribution = await _compute_kind_distribution(session, project.latest_scan_id)

    items: list[dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "id": r.id,
                "license_id": r.license_id,
                "license_spdx_id": r.license_spdx_id,
                "license_name": r.license_name,
                "license_category": r.license_category,
                "kind": r.kind,
                "text": r.text,
                "link": r.link,
                "affected_count": int(r.affected_count),
                "updated_at": r.updated_at,
            }
        )

    return items, distribution, total


async def _compute_kind_distribution(
    session: AsyncSession,
    scan_id: uuid.UUID,
) -> dict[str, int]:
    """
    Per-kind counts of distinct ``(license, kind)`` obligation rows surfaced
    by ``scan_id`` (i.e. whose parent license is observed in the scan).

    Returns the dict ordered by ``_order_distribution`` — known kinds first
    in canonical order, unknown kinds appended alphabetically. The chart
    relies on this ordering for a stable axis even as the catalog grows.
    """
    affected_subq = (
        select(LicenseFinding.license_id.label("license_id"))
        .where(LicenseFinding.scan_id == scan_id)
        .distinct()
        .subquery()
    )
    stmt = (
        select(Obligation.kind.label("kind"), func.count(Obligation.id).label("n"))
        .select_from(Obligation)
        .join(affected_subq, affected_subq.c.license_id == Obligation.license_id)
        .group_by(Obligation.kind)
    )
    result = await session.execute(stmt)
    raw: dict[str, int] = {}
    for row in result.all():
        raw[str(row.kind)] = int(row.n)
    return _order_distribution(raw)


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


async def get_obligation_detail(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    obligation_id: uuid.UUID,
    actor: CurrentUser,
) -> dict[str, Any]:
    """
    Drawer payload for a single obligation, scoped to a project.

    Resolves the project + team via the URL's ``project_id`` and verifies
    the obligation's parent license is observed in that project's latest
    scan. Existence-hides cross-team rows (404 instead of 403) so an
    unauthorized caller cannot discover an obligation id is in use elsewhere
    — same policy as the component, vulnerability, and license drawers.
    """
    project_result = await session.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if project is None:
        # Existence-hide: scoped detail endpoints uniformly 404 if the URL
        # isn't reachable for the caller, regardless of whether the project
        # row genuinely exists.
        raise ObligationNotFound(
            f"obligation {obligation_id} not found in project {project_id}"
        )

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="obligation_detail",
        resource_id=str(obligation_id),
        deny=lambda: ObligationNotFound(
            f"obligation {obligation_id} not found in project {project_id}"
        ),
    )

    obligation_stmt = (
        select(Obligation, LicenseModel)
        .join(LicenseModel, LicenseModel.id == Obligation.license_id)
        .where(Obligation.id == obligation_id)
    )
    row = (await session.execute(obligation_stmt)).first()
    if row is None:
        raise ObligationNotFound(f"obligation {obligation_id} not found")
    obligation, lic = cast(Obligation, row[0]), cast(LicenseModel, row[1])

    # Verify the parent license is materially present in the project's
    # latest scan — otherwise the obligation is a stale catalog handle from
    # this project's perspective and we existence-hide.
    if project.latest_scan_id is None:
        raise ObligationNotFound(
            f"obligation {obligation_id} not visible in project {project_id}"
        )

    # NOTE (v2.2 c4): the drawer is reached only via the list endpoint, which
    # has already materialised the catalog obligations (so the row id this
    # detail call resolves was created there). We deliberately do NOT re-run
    # ``sync_catalog_obligations`` here — the obligation row is fetched above by
    # id, so a sync after the lookup could not surface a not-yet-created row, and
    # keeping detail a pure read avoids an unexpected write on the drawer path.

    presence_stmt = (
        select(LicenseFinding.id)
        .where(LicenseFinding.scan_id == project.latest_scan_id)
        .where(LicenseFinding.license_id == lic.id)
        .limit(1)
    )
    if (await session.execute(presence_stmt)).first() is None:
        raise ObligationNotFound(
            f"obligation {obligation_id} not visible in project {project_id}"
        )

    affected_components, ac_total, ac_truncated = await _load_affected_components(
        session,
        scan_id=project.latest_scan_id,
        license_id=lic.id,
    )

    capped_text, text_truncated = _clamp_obligation_text(obligation.text)

    return {
        "id": obligation.id,
        "license_id": lic.id,
        "license_spdx_id": lic.spdx_id,
        "license_name": lic.name,
        "license_category": lic.category,
        "license_reference_url": lic.reference_url,
        "kind": obligation.kind,
        "text": capped_text,
        "text_truncated": text_truncated,
        "link": obligation.link,
        "affected_components": affected_components,
        "affected_components_truncated": ac_truncated,
        "affected_components_total": ac_total,
        "created_at": obligation.created_at,
        "updated_at": obligation.updated_at,
    }


def _clamp_obligation_text(text: str) -> tuple[str, bool]:
    """Cap obligation text at :data:`_OBLIGATION_TEXT_CAP_BYTES`.

    The DB column is unbounded ``Text``; without this guard a maliciously
    authored or runaway catalog row would inflate the drawer JSON. We clamp
    on the *byte* length (UTF-8) because the cap is a transport-side
    contract, but the public surface is still a unicode ``str`` so we slice
    at the last whole codepoint that fits the byte budget.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= _OBLIGATION_TEXT_CAP_BYTES:
        return text, False
    # Slice at a whole codepoint boundary by decoding the truncated bytes
    # with ``errors="ignore"`` — which drops any partial trailing surrogate.
    capped = encoded[:_OBLIGATION_TEXT_CAP_BYTES].decode("utf-8", errors="ignore")
    return capped, True


async def _load_affected_components(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    license_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], int, bool]:
    """All component_versions in the same scan that carry the parent license,
    capped at :data:`_AFFECTED_COMPONENTS_CAP` rows.

    Returns ``(items, total, truncated)`` mirroring
    :func:`services.license_service._load_affected_components`. The cap is
    applied with ``LIMIT cap+1`` so we detect truncation in the same trip
    as the items query and only pay for an exact ``COUNT(*)`` follow-up
    when the cap actually fired.

    Mirrors :func:`services.license_service._load_affected_components` but
    without the per-finding ``kind`` axis — at obligation granularity the
    user wants "what does this duty cover?", not "which detection kind
    surfaced the parent license".
    """
    cap = _AFFECTED_COMPONENTS_CAP
    stmt = (
        select(
            ComponentVersion.id.label("component_version_id"),
            Component.name.label("component_name"),
            ComponentVersion.version.label("version"),
        )
        .select_from(LicenseFinding)
        .join(ComponentVersion, ComponentVersion.id == LicenseFinding.component_version_id)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(LicenseFinding.scan_id == scan_id)
        .where(LicenseFinding.license_id == license_id)
        .group_by(ComponentVersion.id, Component.name, ComponentVersion.version)
        .order_by(Component.name.asc(), ComponentVersion.version.asc())
        .limit(cap + 1)
    )
    rows = (await session.execute(stmt)).all()
    truncated = len(rows) > cap
    items = [
        {
            "component_version_id": r.component_version_id,
            "component_name": r.component_name,
            "version": r.version,
        }
        for r in rows[:cap]
    ]
    if not truncated:
        total = len(items)
    else:
        count_stmt = (
            select(func.count())
            .select_from(
                select(ComponentVersion.id)
                .select_from(LicenseFinding)
                .join(
                    ComponentVersion,
                    ComponentVersion.id == LicenseFinding.component_version_id,
                )
                .where(LicenseFinding.scan_id == scan_id)
                .where(LicenseFinding.license_id == license_id)
                .group_by(ComponentVersion.id)
                .subquery()
            )
        )
        total = int((await session.execute(count_stmt)).scalar_one())
    return items, total, truncated


# ---------------------------------------------------------------------------
# NOTICE generator
# ---------------------------------------------------------------------------


_NOTICE_DIVIDER = "=" * 80

# CommonMark inline-active metacharacters we backslash-escape in untrusted
# values so a value can never open an emphasis run, link, image, code span, or
# table cell when interpolated MID-LINE (which is the only way this module
# interpolates untrusted text — after ``## ``, ``- ``, ``Reference: ``, etc.).
# Line-start-only markers (``#`` ``-`` ``+`` ``.`` ``>``) are deliberately NOT
# backslash-escaped: they are inert mid-line and escaping ``.``/``-`` would
# corrupt every version string (``1\.0\.0``) for no security gain. Their safety
# relies on the value never reaching column 0 — which is why ``_md_escape`` must
# also COLLAPSE embedded newlines (a value containing ``\n## x`` would otherwise
# push ``## x`` to line-start and inject a live heading). The angle brackets and
# ampersand are handled separately by an HTML-escape so a markdown→HTML render
# cannot execute an embedded ``<script>``.
_MD_INLINE_PUNCTUATION = frozenset("\\`*_[]()~|")


def _md_escape(value: str | None) -> str:
    """Escape untrusted text for safe interpolation into the markdown NOTICE.

    The markdown NOTICE interpolates component / license names, obligation text
    and reference URLs that all originate from scanned third-party metadata
    (untrusted). If a downstream viewer renders the markdown as HTML, an embedded
    ``<script>`` or a ``[x](javascript:…)`` link would execute. We:

      1. HTML-escape ``&`` / ``<`` / ``>`` so raw HTML tags become inert text in
         a markdown→HTML render, and
      2. backslash-escape the CommonMark inline-active punctuation
         (:data:`_MD_INLINE_PUNCTUATION`) so the value is shown literally and
         cannot start an emphasis run, link, image, code span, or table cell.

    Decision (G2 — markdown escape vs document-as-unsafe): we ESCAPE rather than
    declare the format unsafe. The untrusted fields here are attribution data
    (``name @ version``, a license name, an obligation paragraph), not authored
    markdown — escaping only the inline-active metacharacters keeps legitimate
    attribution readable (a version string survives intact) while making the
    markdown output safe even for a viewer that pipes it through an HTML
    renderer. This mirrors the html branch's escape posture (every interpolated
    value is neutralised).

    Mid-line interpolation is what makes leaving the line-start-only markers
    (``#`` ``-`` ``.`` ``>``) un-escaped safe — BUT only if the value cannot
    smuggle its own newline. A value such as ``"\n## INJECTED\n---\n> q"`` would
    otherwise carry ``## INJECTED`` / ``---`` / ``> q`` to column 0 and inject a
    live heading / thematic break / blockquote into the rendered NOTICE (content
    & attribution spoofing of a legal artifact). We therefore collapse every
    line break to a single space as the final step, guaranteeing the value stays
    on one (interpolated, mid-line) line.
    """
    if not value:
        return ""
    # HTML-escape first (so a literal backslash we add next is not itself
    # double-handled), then backslash-escape the inline markdown punctuation.
    escaped = html_escape(value, quote=False)
    out: list[str] = []
    for ch in escaped:
        if ch in _MD_INLINE_PUNCTUATION:
            out.append("\\" + ch)
        else:
            out.append(ch)
    result = "".join(out)
    # Collapse line breaks so an interpolated value can never reach line-start
    # (where ``#``/``-``/``>`` would become live markdown structure).
    result = result.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return result


async def generate_notice(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    fmt: str = "text",
) -> dict[str, Any]:
    """
    Compose a NOTICE attribution body for the project's latest scan.

    Output shape (text format)::

        Third-party Licenses for <project_name>
        Generated: <ISO8601 UTC>

        ================================================================================

        <SPDX-ID> — <license name>
        Components:
          - foo@1.2.3
          - bar@4.5.6

        Obligation: <kind>
        <obligation_text>
        Reference: <obligation_link>  (omitted when null)

        ================================================================================
        ...

    A license that has zero obligations on file still appears so its
    components are credited; the obligation block is replaced with a
    ``(no obligations recorded)`` line so the document is unambiguous about
    what the catalog covers.

    The markdown variant uses H2 headings, fenced code blocks for the
    component list, and bold labels. The html variant emits a complete,
    self-contained HTML document with every interpolated value escaped
    (component names / license texts / reference URLs come from scanned
    package metadata and are untrusted), suitable for inline viewing or
    download.

    Returns the raw ``body`` text plus provenance fields a router can hand
    to ``Content-Disposition`` and inspection headers.
    """
    if fmt not in {"text", "markdown", "html"}:
        raise ObligationError(f"unsupported format: {fmt!r}")

    project_result = await session.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_notice",
        resource_id=str(project_id),
        # Existence-hide cross-team reads as 404 (security-reviewer Low #4):
        # uniform with the obligation-detail / vulnerability / license / SBOM /
        # report / source-tree endpoints so a non-member cannot distinguish
        # "exists but forbidden" from "does not exist".
        deny=lambda: ProjectNotFound(f"project {project_id} not found"),
    )

    generated_at = datetime.now(tz=UTC)

    if project.latest_scan_id is None:
        body = _render_empty_notice(project.name, generated_at, fmt=fmt)
        return {
            "project_id": project.id,
            "project_name": project.name,
            "generated_at": generated_at,
            "format": fmt,
            "body": body,
            "license_count": 0,
            "obligation_count": 0,
        }

    # v2.2 c4: materialise the structured obligation catalog for this scan's
    # licenses so the NOTICE renders attribution / source-disclosure / patent
    # guidance instead of "(no obligations recorded)". Idempotent + additive.
    await sync_catalog_obligations(session, scan_id=project.latest_scan_id)

    licenses_with_components, obligations_by_license, licenses_omitted = (
        await _load_notice_data(session, scan_id=project.latest_scan_id)
    )

    body = _render_notice(
        project_name=project.name,
        generated_at=generated_at,
        licenses_with_components=licenses_with_components,
        obligations_by_license=obligations_by_license,
        fmt=fmt,
        licenses_omitted=licenses_omitted,
    )

    # Inspection headers report the TRUE totals (rendered head + omitted tail)
    # so the X-Notice-* counts stay honest even when a body cap clipped the
    # document. ``obligations_omitted`` rides on each license entry.
    rendered_obligations = sum(len(rows) for rows in obligations_by_license.values())
    obligations_omitted_total = sum(
        int(e.get("obligations_omitted", 0)) for e in licenses_with_components
    )
    return {
        "project_id": project.id,
        "project_name": project.name,
        "generated_at": generated_at,
        "format": fmt,
        "body": body,
        "license_count": len(licenses_with_components) + licenses_omitted,
        "obligation_count": rendered_obligations + obligations_omitted_total,
    }


async def _load_notice_data(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
) -> tuple[
    list[dict[str, Any]],
    dict[uuid.UUID, list[dict[str, Any]]],
    int,
]:
    """Two trips: licenses + components, then per-license obligation rows.

    Returns ``(licenses_with_components, obligations_by_license, licenses_omitted)``.

    Body-size caps (G2 follow-up):
      - the number of license SECTIONS is capped at ``_NOTICE_LICENSE_CAP``; the
        excess count is returned as ``licenses_omitted`` so the renderer can add
        an honest footer instead of silently dropping sections.
      - each license entry carries ``obligations_omitted`` — the number of
        obligation rows beyond ``_NOTICE_OBLIGATIONS_PER_LICENSE_CAP`` that were
        clipped from that license's section.
    """
    license_stmt = (
        select(
            LicenseModel.id.label("license_id"),
            LicenseModel.spdx_id.label("spdx_id"),
            LicenseModel.name.label("name"),
            LicenseModel.reference_url.label("reference_url"),
            func.array_agg(
                func.distinct(
                    Component.name.op("||")(" @ ").op("||")(ComponentVersion.version)
                )
            ).label("component_labels"),
        )
        .select_from(LicenseFinding)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .join(ComponentVersion, ComponentVersion.id == LicenseFinding.component_version_id)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(LicenseFinding.scan_id == scan_id)
        .group_by(
            LicenseModel.id,
            LicenseModel.spdx_id,
            LicenseModel.name,
            LicenseModel.reference_url,
        )
        .order_by(LicenseModel.spdx_id.asc().nullslast(), LicenseModel.name.asc())
    )
    all_license_rows = (await session.execute(license_stmt)).all()
    # G2 follow-up: cap the number of license SECTIONS. The query is already
    # deterministically ordered (spdx_id asc nullslast, name asc) so the kept
    # head is stable across runs; the tail is recorded as an honest footer.
    licenses_omitted = max(len(all_license_rows) - _NOTICE_LICENSE_CAP, 0)
    license_rows = all_license_rows[:_NOTICE_LICENSE_CAP]
    licenses_with_components: list[dict[str, Any]] = []
    license_ids: list[uuid.UUID] = []
    for r in license_rows:
        license_ids.append(r.license_id)
        all_labels = sorted(label for label in (r.component_labels or []) if label)
        # G2: cap the credited-component list with an honest omitted-count so a
        # license attached to a pathological number of components cannot inflate
        # the body. Renderers append a "+N more omitted" note when this fires.
        labels = all_labels[:_NOTICE_COMPONENT_LABELS_CAP]
        labels_omitted = max(len(all_labels) - len(labels), 0)
        # G2: clamp the license display name defensively (the column is bounded
        # in practice, but the NOTICE body must not trust any single field).
        clamped_name, _name_truncated = (
            _clamp_obligation_text(r.name) if r.name else (r.name, False)
        )
        licenses_with_components.append(
            {
                "license_id": r.license_id,
                "spdx_id": r.spdx_id,
                "name": clamped_name,
                "reference_url": r.reference_url,
                "component_labels": labels,
                "component_labels_omitted": labels_omitted,
                # Filled in below once the per-license obligation rows are capped.
                "obligations_omitted": 0,
            }
        )

    obligations_by_license: dict[uuid.UUID, list[dict[str, Any]]] = {
        lid: [] for lid in license_ids
    }
    if license_ids:
        ob_stmt = (
            select(Obligation)
            .where(Obligation.license_id.in_(license_ids))
            .order_by(Obligation.license_id.asc(), Obligation.kind.asc())
        )
        ob_rows = (await session.execute(ob_stmt)).scalars().all()
        # Count how many obligations each license carries BEFORE clamping so we
        # can record the per-license omitted tail. The ordered fetch above means
        # the kept head (first ``_NOTICE_OBLIGATIONS_PER_LICENSE_CAP`` rows by
        # kind) is stable across runs.
        seen_per_license: dict[uuid.UUID, int] = {}
        omitted_per_license: dict[uuid.UUID, int] = {}
        for ob in ob_rows:
            kept = seen_per_license.get(ob.license_id, 0)
            if kept >= _NOTICE_OBLIGATIONS_PER_LICENSE_CAP:
                # G2 follow-up: cap the per-license obligation list. We stop
                # appending past the cap and tally the excess for an honest note.
                omitted_per_license[ob.license_id] = (
                    omitted_per_license.get(ob.license_id, 0) + 1
                )
                continue
            # G2: clamp obligation text at the same 64 KiB byte budget the
            # drawer uses so a runaway catalog row cannot bloat the NOTICE body.
            clamped_text, _text_truncated = _clamp_obligation_text(ob.text or "")
            obligations_by_license.setdefault(ob.license_id, []).append(
                {
                    "kind": ob.kind,
                    "text": clamped_text,
                    "link": ob.link,
                }
            )
            seen_per_license[ob.license_id] = kept + 1

        if omitted_per_license:
            for entry in licenses_with_components:
                entry["obligations_omitted"] = omitted_per_license.get(
                    entry["license_id"], 0
                )

    return licenses_with_components, obligations_by_license, licenses_omitted


def _render_empty_notice(project_name: str, generated_at: datetime, *, fmt: str) -> str:
    """Body for projects with no scan yet — keep the document well-formed."""
    if fmt == "html":
        return _render_notice_html(
            project_name=project_name,
            generated_at=generated_at,
            licenses_with_components=[],
            obligations_by_license={},
            empty_reason="No scan has been run for this project yet.",
        )
    header = _format_header(project_name, generated_at, fmt=fmt)
    if fmt == "markdown":
        return f"{header}\n\n_No scan has been run for this project yet._\n"
    return f"{header}\n\n(no scan has been run for this project yet)\n"


def _format_header(project_name: str, generated_at: datetime, *, fmt: str) -> str:
    iso = generated_at.replace(microsecond=0).isoformat()
    if fmt == "markdown":
        return f"# Third-party Licenses for {project_name}\n\nGenerated: `{iso}`"
    return f"Third-party Licenses for {project_name}\nGenerated: {iso}"


def _render_notice(
    *,
    project_name: str,
    generated_at: datetime,
    licenses_with_components: list[dict[str, Any]],
    obligations_by_license: dict[uuid.UUID, list[dict[str, Any]]],
    fmt: str,
    licenses_omitted: int = 0,
) -> str:
    if fmt == "html":
        return _render_notice_html(
            project_name=project_name,
            generated_at=generated_at,
            licenses_with_components=licenses_with_components,
            obligations_by_license=obligations_by_license,
            licenses_omitted=licenses_omitted,
        )

    parts: list[str] = [_format_header(project_name, generated_at, fmt=fmt), ""]

    if fmt == "markdown":
        # G2/markdown-escape: every interpolated value below is untrusted
        # (scanned third-party metadata). We route it through ``_md_escape`` so a
        # markdown→HTML renderer cannot execute embedded ``<script>`` or
        # ``[x](javascript:…)``. ``spdx_id`` is also escaped defensively.
        for entry in licenses_with_components:
            parts.append("---")
            parts.append("")
            spdx = _md_escape(entry["spdx_id"]) if entry["spdx_id"] else "(no SPDX id)"
            parts.append(f"## {spdx} — {_md_escape(entry['name'])}")
            parts.append("")
            if entry["reference_url"]:
                # Clamp to 2048 chars (mirrors the html branch's ``_safe_href``
                # cap) so an attacker-controlled metadata URL can't bloat the
                # document with a multi-KiB link.
                parts.append(f"Reference: {_md_escape(entry['reference_url'][:2048])}")
                parts.append("")
            parts.append("**Components:**")
            parts.append("")
            # Escaped bullet list (not a fenced block) so a label containing a
            # ``` fence delimiter cannot break out and so each label is inert.
            for label in entry["component_labels"]:
                parts.append(f"- {_md_escape(label)}")
            omitted = entry.get("component_labels_omitted", 0)
            if omitted:
                parts.append(f"- _… and {omitted} more component(s) omitted_")
            parts.append("")
            obs = obligations_by_license.get(entry["license_id"], [])
            if not obs:
                parts.append("_No obligations recorded for this license._")
                parts.append("")
            else:
                for ob in obs:
                    parts.append(f"**Obligation: {_md_escape(ob['kind'])}**")
                    parts.append("")
                    parts.append(_md_escape(ob["text"]))
                    if ob["link"]:
                        parts.append("")
                        # Clamp to 2048 chars (mirrors the html ``_safe_href`` cap).
                        parts.append(f"Reference: {_md_escape(ob['link'][:2048])}")
                    parts.append("")
                obs_omitted = entry.get("obligations_omitted", 0)
                if obs_omitted:
                    parts.append(
                        f"_… and {obs_omitted} more obligation(s) omitted for "
                        "this license._"
                    )
                    parts.append("")
        parts.append("---")
        parts.append("")
        if licenses_omitted:
            parts.append(
                f"_… and {licenses_omitted} more license(s) omitted from this "
                "NOTICE._"
            )
            parts.append("")
        return "\n".join(parts).rstrip() + "\n"

    # plain text
    for entry in licenses_with_components:
        parts.append(_NOTICE_DIVIDER)
        parts.append("")
        spdx = entry["spdx_id"] or "(no SPDX id)"
        parts.append(f"{spdx} — {entry['name']}")
        if entry["reference_url"]:
            parts.append(f"Reference: {entry['reference_url']}")
        parts.append("")
        parts.append("Components:")
        for label in entry["component_labels"]:
            parts.append(f"  - {label}")
        omitted = entry.get("component_labels_omitted", 0)
        if omitted:
            parts.append(f"  - ... and {omitted} more component(s) omitted")
        parts.append("")
        obs = obligations_by_license.get(entry["license_id"], [])
        if not obs:
            parts.append("(no obligations recorded for this license)")
            parts.append("")
        else:
            for ob in obs:
                parts.append(f"Obligation: {ob['kind']}")
                parts.append(ob["text"])
                if ob["link"]:
                    parts.append(f"Reference: {ob['link']}")
                parts.append("")
            obs_omitted = entry.get("obligations_omitted", 0)
            if obs_omitted:
                parts.append(
                    f"... and {obs_omitted} more obligation(s) omitted for this "
                    "license"
                )
                parts.append("")
    parts.append(_NOTICE_DIVIDER)
    parts.append("")
    if licenses_omitted:
        parts.append(
            f"... and {licenses_omitted} more license(s) omitted from this NOTICE"
        )
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# A compact, self-contained stylesheet so the downloaded .html renders well
# offline (no external assets). Mirrors the portal's enterprise-SCA palette.
_NOTICE_HTML_STYLE = (
    "body{font-family:Inter,system-ui,-apple-system,sans-serif;max-width:880px;"
    "margin:2rem auto;padding:0 1rem;color:#0f172a;line-height:1.5}"
    "h1{font-size:1.5rem;border-bottom:2px solid #0f172a;padding-bottom:.3rem}"
    "h2{font-size:1.1rem;margin-top:1.5rem}"
    "h3{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;"
    "color:#64748b;margin:.6rem 0 .3rem}"
    "section.license{border-top:1px solid #e2e8f0;padding-top:.4rem}"
    "ul{margin:.3rem 0}"
    'code,pre{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.85rem}'
    "pre{background:#f8fafc;padding:.6rem;border-radius:4px;white-space:pre-wrap;"
    "word-break:break-word}"
    ".generated{color:#64748b;font-size:.85rem}"
    ".no-obligations{color:#64748b;font-style:italic}"
    "li.muted{color:#64748b;font-style:italic;list-style:none}"
    "p.muted{color:#64748b;font-style:italic}"
    ".obligation{margin:.5rem 0}"
)


def _safe_href(url: str | None) -> str | None:
    """Return an attribute-escaped href, but only for http(s) URLs.

    The reference URLs originate from scanned package / license metadata,
    which is untrusted. Emitting them into an ``href`` verbatim would allow
    ``javascript:``/``data:``/``file:`` scheme injection (stored XSS in the
    downloaded NOTICE). We allow only http/https and escape quotes; anything
    else falls back to plain escaped text at the call site.
    """
    if not url:
        return None
    stripped = url.strip()
    # Bound the href length so an attacker-controlled metadata URL can't bloat
    # the document via a multi-KiB link (defence in depth; a holistic body-size
    # cap across all NOTICE formats is tracked as a separate follow-up).
    if len(stripped) > 2048:
        return None
    if stripped.lower().startswith(("http://", "https://")):
        return html_escape(stripped, quote=True)
    return None


def _render_notice_html(
    *,
    project_name: str,
    generated_at: datetime,
    licenses_with_components: list[dict[str, Any]],
    obligations_by_license: dict[uuid.UUID, list[dict[str, Any]]],
    empty_reason: str | None = None,
    licenses_omitted: int = 0,
) -> str:
    """Render the NOTICE as a complete, fully-escaped HTML document."""
    iso = generated_at.replace(microsecond=0).isoformat()
    esc_name = html_escape(project_name)
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Third-party Licenses for {esc_name}</title>",
        f"<style>{_NOTICE_HTML_STYLE}</style>",
        "</head>",
        "<body>",
        f"<h1>Third-party Licenses for {esc_name}</h1>",
        f'<p class="generated">Generated: <code>{html_escape(iso)}</code></p>',
    ]

    if empty_reason is not None:
        parts.append(f"<p>{html_escape(empty_reason)}</p>")
    elif not licenses_with_components:
        parts.append("<p>(no third-party licenses detected for this project)</p>")
    else:
        for entry in licenses_with_components:
            parts.append('<section class="license">')
            spdx = entry["spdx_id"] or "(no SPDX id)"
            parts.append(
                f"<h2>{html_escape(spdx)} — {html_escape(entry['name'] or '')}</h2>"
            )
            parts.append(_html_reference_line(entry["reference_url"]))
            parts.append("<h3>Components</h3>")
            parts.append("<ul>")
            for label in entry["component_labels"]:
                parts.append(f"<li>{html_escape(label)}</li>")
            omitted = entry.get("component_labels_omitted", 0)
            if omitted:
                parts.append(
                    f'<li class="muted">… and {omitted} more component(s) '
                    "omitted</li>"
                )
            parts.append("</ul>")
            obs = obligations_by_license.get(entry["license_id"], [])
            if not obs:
                parts.append(
                    '<p class="no-obligations">'
                    "No obligations recorded for this license.</p>"
                )
            else:
                for ob in obs:
                    parts.append('<div class="obligation">')
                    parts.append(
                        f"<p><strong>Obligation: {html_escape(ob['kind'])}</strong></p>"
                    )
                    parts.append(f"<pre>{html_escape(ob['text'] or '')}</pre>")
                    parts.append(_html_reference_line(ob["link"]))
                    parts.append("</div>")
                obs_omitted = entry.get("obligations_omitted", 0)
                if obs_omitted:
                    parts.append(
                        f'<p class="muted">… and {obs_omitted} more obligation(s) '
                        "omitted for this license.</p>"
                    )
            parts.append("</section>")

        if licenses_omitted:
            parts.append(
                f'<p class="muted">… and {licenses_omitted} more license(s) '
                "omitted from this NOTICE.</p>"
            )

    parts.append("</body>")
    parts.append("</html>")
    # Drop the empty strings _html_reference_line returns for absent URLs.
    return "\n".join(p for p in parts if p) + "\n"


def _html_reference_line(url: str | None) -> str:
    """A ``Reference:`` paragraph for an optional URL, or "" when absent.

    Linkifies only safe http(s) URLs; other schemes degrade to escaped text.
    """
    if not url:
        return ""
    href = _safe_href(url)
    if href is not None:
        return (
            f'<p class="reference">Reference: '
            f'<a href="{href}" rel="noopener noreferrer">{html_escape(url)}</a></p>'
        )
    return f'<p class="reference">Reference: {html_escape(url)}</p>'


__all__ = [
    "ObligationError",
    "ObligationNotFound",
    "generate_notice",
    "get_obligation_detail",
    "list_project_obligations",
    "sync_catalog_obligations",
]
