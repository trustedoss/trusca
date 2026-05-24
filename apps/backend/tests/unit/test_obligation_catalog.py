"""
Tests for the structured obligation catalog — v2.2 Track C (c4).

Two layers, mirroring :file:`tests/unit/test_obligation_service.py`:

  - Pure catalog cases (no DB) — assert the structured obligation FACTS for the
    spot-check licenses (MIT / Apache-2.0 / GPL / LGPL / AGPL / SSPL / BSD / ISC)
    and the resolution behaviour (unknown id, empty id, compound expression).
    These run on every PR with no Postgres dependency.
  - DB-backed cases (``integration`` marker) — assert that
    ``services.obligation_service.sync_catalog_obligations`` idempotently
    populates the ``obligations`` table for the licenses a scan observed, never
    overwrites a pre-existing row, and is a no-op on re-run.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.obligation_catalog import (
    KIND_ATTRIBUTION,
    KIND_COPYLEFT,
    KIND_MODIFICATIONS,
    KIND_NOTICE,
    KIND_PATENT,
    KIND_SOURCE_DISCLOSURE,
    SourceDisclosure,
    catalog_spdx_ids,
    get_license_obligations,
    obligations_for,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Pure catalog correctness — structured obligation facts (no DB).
# ---------------------------------------------------------------------------


def test_catalog_covers_the_scan_source_license_defaults() -> None:
    """The obligation catalog must cover every license the classifier knows.

    ``tasks.scan_source._LICENSE_CATEGORY_DEFAULTS`` is the set of licenses the
    portal categorises; the obligation catalog must enrich the SAME set so a
    classified license never lands with zero obligations.
    """
    from tasks.scan_source import _LICENSE_CATEGORY_DEFAULTS

    missing = set(_LICENSE_CATEGORY_DEFAULTS) - catalog_spdx_ids()
    assert missing == set(), f"licenses classified but not in obligation catalog: {missing}"


def test_mit_requires_attribution_and_license_text() -> None:
    o = get_license_obligations("MIT")
    assert o is not None
    assert o.attribution_required is True
    assert o.license_text_inclusion_required is True
    assert o.copyright_notice_required is True
    assert o.patent_grant is False
    assert o.source_disclosure is SourceDisclosure.NONE
    assert o.same_license_required is False
    kinds = {k for k, _, _ in obligations_for("MIT")}
    assert kinds == {KIND_ATTRIBUTION, KIND_NOTICE}


def test_isc_matches_mit_attribution_shape() -> None:
    o = get_license_obligations("ISC")
    assert o is not None
    assert o.attribution_required is True
    assert o.license_text_inclusion_required is True
    assert o.source_disclosure is SourceDisclosure.NONE


def test_apache_2_0_has_attribution_text_patent_and_state_changes() -> None:
    o = get_license_obligations("Apache-2.0")
    assert o is not None
    assert o.attribution_required is True
    assert o.license_text_inclusion_required is True
    assert o.patent_grant is True
    assert o.state_changes_required is True
    assert o.notice_file_required is True
    assert o.source_disclosure is SourceDisclosure.NONE
    kinds = {k for k, _, _ in obligations_for("Apache-2.0")}
    assert KIND_PATENT in kinds
    assert KIND_MODIFICATIONS in kinds
    assert KIND_NOTICE in kinds


def test_gpl_3_0_is_program_copyleft_with_source_disclosure() -> None:
    o = get_license_obligations("GPL-3.0-only")
    assert o is not None
    # GPL reaches the whole conveyed program (same_license_required), with the
    # source-disclosure obligation triggered by conveying a binary.
    assert o.same_license_required is True
    assert o.source_disclosure is SourceDisclosure.LIBRARY
    assert o.patent_grant is True  # GPLv3 §11
    kinds = {k for k, _, _ in obligations_for("GPL-3.0-only")}
    assert KIND_SOURCE_DISCLOSURE in kinds
    assert KIND_COPYLEFT in kinds


def test_lgpl_is_library_scoped_source_disclosure_not_whole_work() -> None:
    o = get_license_obligations("LGPL-2.1-only")
    assert o is not None
    assert o.source_disclosure is SourceDisclosure.LIBRARY
    # The distinguishing LGPL property: only the LIBRARY is copyleft, not the
    # whole application that links to it.
    assert o.same_license_required is False
    kinds = {k for k, _, _ in obligations_for("LGPL-2.1-only")}
    assert KIND_SOURCE_DISCLOSURE in kinds


@pytest.mark.parametrize("spdx", ["AGPL-3.0-only", "AGPL-3.0-or-later", "SSPL-1.0"])
def test_network_copyleft_licenses_have_network_source_disclosure(spdx: str) -> None:
    o = get_license_obligations(spdx)
    assert o is not None
    assert o.source_disclosure is SourceDisclosure.NETWORK
    assert o.same_license_required is True
    kinds = {k for k, _, _ in obligations_for(spdx)}
    assert KIND_SOURCE_DISCLOSURE in kinds


@pytest.mark.parametrize("spdx", ["BSD-2-Clause", "BSD-3-Clause"])
def test_bsd_requires_attribution_and_license_text(spdx: str) -> None:
    o = get_license_obligations(spdx)
    assert o is not None
    assert o.attribution_required is True
    assert o.license_text_inclusion_required is True
    assert o.source_disclosure is SourceDisclosure.NONE
    kinds = {k for k, _, _ in obligations_for(spdx)}
    assert KIND_ATTRIBUTION in kinds


@pytest.mark.parametrize("spdx", ["0BSD", "Unlicense", "CC0-1.0", "WTFPL"])
def test_public_domain_licenses_have_no_obligations(spdx: str) -> None:
    o = get_license_obligations(spdx)
    assert o is not None
    assert o.attribution_required is False
    assert obligations_for(spdx) == []


def test_obligations_for_attaches_reference_url_as_link() -> None:
    rows = obligations_for("MIT", reference_url="https://spdx.org/licenses/MIT.html")
    assert rows  # MIT has obligations
    assert all(link == "https://spdx.org/licenses/MIT.html" for _, _, link in rows)


def test_obligations_for_link_is_none_without_reference_url() -> None:
    rows = obligations_for("MIT")
    assert all(link is None for _, _, link in rows)


# ---------------------------------------------------------------------------
# Adversarial / edge resolution behaviour (no DB).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spdx", [None, "", "LicenseRef-acme-custom", "TotallyNotASpdxId"])
def test_unknown_or_empty_spdx_yields_no_obligations(spdx: str | None) -> None:
    assert obligations_for(spdx) == []
    assert get_license_obligations(spdx) is None


def test_compound_expression_unions_operand_obligations() -> None:
    """A compound SPDX expression returns the UNION of recognised operands."""
    union = {k for k, _, _ in obligations_for("MIT OR GPL-3.0-only")}
    mit = {k for k, _, _ in obligations_for("MIT")}
    gpl = {k for k, _, _ in obligations_for("GPL-3.0-only")}
    assert union == (mit | gpl)


def test_compound_with_unknown_operand_keeps_known_obligations() -> None:
    rows = obligations_for("MIT OR LicenseRef-mystery")
    kinds = {k for k, _, _ in rows}
    assert kinds == {KIND_ATTRIBUTION, KIND_NOTICE}


def test_compound_with_exception_operator_resolves_base_license() -> None:
    # "Apache-2.0 WITH LLVM-exception" — the WITH operand is unknown, so the
    # base Apache-2.0 obligations carry through.
    kinds = {k for k, _, _ in obligations_for("Apache-2.0 WITH LLVM-exception")}
    assert KIND_PATENT in kinds
    assert KIND_ATTRIBUTION in kinds


def test_compound_all_unknown_operands_yields_no_obligations() -> None:
    assert obligations_for("Foo-1.0 OR Bar-2.0") == []


def test_obligations_for_deduplicates_repeated_obligation_rows() -> None:
    """A compound whose operands share a (kind, text) must not duplicate it."""
    # "MIT OR MIT" exercises the operand-level de-dup (same operand twice).
    rows = obligations_for("MIT OR MIT")
    keys = [(k, t) for k, t, _ in rows]
    assert len(keys) == len(set(keys))


def test_obligations_for_deduplicates_across_distinct_operands() -> None:
    """Two DIFFERENT operands sharing a (kind, text) must collapse to one row.

    MIT and ISC emit the identical attribution + notice paragraphs, so
    ``MIT OR ISC`` must not double them — this exercises the (kind, text)
    de-dup across distinct catalog entries.
    """
    rows = obligations_for("MIT OR ISC")
    keys = [(k, t) for k, t, _ in rows]
    assert len(keys) == len(set(keys))
    # Same shape as MIT alone (ISC adds nothing new).
    assert {k for k, _, _ in rows} == {KIND_ATTRIBUTION, KIND_NOTICE}


def test_every_catalog_entry_self_consistent() -> None:
    """Structured flags must agree with the rendered obligation rows."""
    for spdx in catalog_spdx_ids():
        o = get_license_obligations(spdx)
        assert o is not None
        kinds = {k for k, _, _ in obligations_for(spdx)}
        if o.patent_grant:
            assert KIND_PATENT in kinds, f"{spdx} claims patent grant but no patent row"
        if o.source_disclosure is not SourceDisclosure.NONE:
            assert KIND_SOURCE_DISCLOSURE in kinds, f"{spdx} discloses source but no row"
        if o.same_license_required:
            assert KIND_COPYLEFT in kinds, f"{spdx} is copyleft but has no copyleft row"


# ---------------------------------------------------------------------------
# DB-backed: sync_catalog_obligations idempotent population.
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip obligation catalog DB tests")
    return url


@pytest.fixture(scope="module")
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; obligation catalog DB tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session(_migrate_once) -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)

    async with factory() as session:
        yield session

    await engine.dispose()


async def _make_component_version(session: AsyncSession):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    purl = f"pkg:npm/cat-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"cat-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)
    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return cv


async def _make_license(session: AsyncSession, *, spdx_id: str, category: str):
    from models import License as LicenseModel

    # ``licenses.spdx_id`` is GLOBALLY unique (shared catalog across all scans),
    # so get-or-create rather than blind insert: a well-known SPDX id may have
    # been materialised by an earlier test against the same DB. The reference_url
    # is set on first creation so the deep-link assertions hold.
    existing = (
        await session.execute(
            select(LicenseModel).where(LicenseModel.spdx_id == spdx_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    lic = LicenseModel(
        spdx_id=spdx_id,
        name=f"{spdx_id} name",
        category=category,
        reference_url=f"https://spdx.org/licenses/{spdx_id}.html",
    )
    session.add(lic)
    await session.commit()
    await session.refresh(lic)
    return lic


async def _attach_finding(session: AsyncSession, *, scan_id, cv_id, license_id):
    from models import LicenseFinding

    lf = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        license_id=license_id,
        kind="declared",
        source_path=None,
        raw_data={},
    )
    session.add(lf)
    await session.commit()
    await session.refresh(lf)
    return lf


async def _project_with_scan(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    project.updated_at = datetime.now(tz=UTC)
    await session.commit()
    await session.refresh(project)
    return project, scan


@pytest.mark.asyncio
async def test_sync_populates_obligations_for_observed_licenses(db_session: AsyncSession) -> None:
    from sqlalchemy import delete

    from models import Obligation
    from services.obligation_service import sync_catalog_obligations

    _project, scan = await _project_with_scan(db_session)
    cv = await _make_component_version(db_session)
    mit = await _make_license(db_session, spdx_id="MIT", category="allowed")
    gpl = await _make_license(db_session, spdx_id="GPL-3.0-only", category="forbidden")
    # The licenses catalog is shared/global; clear any obligations a prior test
    # synced for MIT / GPL so the insert count is deterministic here.
    await db_session.execute(
        delete(Obligation).where(Obligation.license_id.in_([mit.id, gpl.id]))
    )
    await db_session.commit()
    await _attach_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=mit.id)
    await _attach_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=gpl.id)

    inserted = await sync_catalog_obligations(db_session, scan_id=scan.id)
    await db_session.commit()
    assert inserted > 0

    mit_kinds = {
        o.kind
        for o in (
            await db_session.execute(select(Obligation).where(Obligation.license_id == mit.id))
        ).scalars()
    }
    assert mit_kinds == {KIND_ATTRIBUTION, KIND_NOTICE}

    gpl_kinds = {
        o.kind
        for o in (
            await db_session.execute(select(Obligation).where(Obligation.license_id == gpl.id))
        ).scalars()
    }
    assert KIND_SOURCE_DISCLOSURE in gpl_kinds
    assert KIND_COPYLEFT in gpl_kinds

    # Link deep-links to the license reference_url.
    one = (
        await db_session.execute(select(Obligation).where(Obligation.license_id == mit.id))
    ).scalars().first()
    assert one is not None
    assert one.link == "https://spdx.org/licenses/MIT.html"


@pytest.mark.asyncio
async def test_sync_is_idempotent_on_rerun(db_session: AsyncSession) -> None:
    from sqlalchemy import delete

    from models import Obligation
    from services.obligation_service import sync_catalog_obligations

    _project, scan = await _project_with_scan(db_session)
    cv = await _make_component_version(db_session)
    # EPL-2.0 is in the catalog and unused by the other DB tests. Clear any
    # obligations a prior run synced for this shared catalog row so the first
    # sync below starts from a known-empty state (order-independent).
    lic = await _make_license(db_session, spdx_id="EPL-2.0", category="conditional")
    await db_session.execute(delete(Obligation).where(Obligation.license_id == lic.id))
    await db_session.commit()
    await _attach_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)

    first = await sync_catalog_obligations(db_session, scan_id=scan.id)
    await db_session.commit()
    assert first > 0

    second = await sync_catalog_obligations(db_session, scan_id=scan.id)
    await db_session.commit()
    assert second == 0  # nothing missing the second time

    count = len(
        (
            await db_session.execute(select(Obligation).where(Obligation.license_id == lic.id))
        ).scalars().all()
    )
    assert count == first


@pytest.mark.asyncio
async def test_sync_never_overwrites_existing_obligation(db_session: AsyncSession) -> None:
    """A seed-/operator-authored (license, kind) row must survive a sync."""
    from sqlalchemy import delete

    from models import Obligation
    from services.obligation_service import sync_catalog_obligations

    _project, scan = await _project_with_scan(db_session)
    cv = await _make_component_version(db_session)
    # BSD-3-Clause carries an ``attribution`` obligation in the catalog and is
    # not used by the other DB tests. The ``licenses`` catalog is shared/global,
    # so clear any obligations a prior test may have synced for this row before
    # pre-authoring the custom one (keeps the test order-independent).
    lic = await _make_license(db_session, spdx_id="BSD-3-Clause", category="allowed")
    await db_session.execute(delete(Obligation).where(Obligation.license_id == lic.id))
    await db_session.commit()
    await _attach_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)

    # Pre-author the (BSD-3-Clause, attribution) row with custom text.
    custom = Obligation(license_id=lic.id, kind=KIND_ATTRIBUTION, text="CUSTOM TEXT", link=None)
    db_session.add(custom)
    await db_session.commit()

    await sync_catalog_obligations(db_session, scan_id=scan.id)
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(Obligation).where(
                Obligation.license_id == lic.id, Obligation.kind == KIND_ATTRIBUTION
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "CUSTOM TEXT"  # never clobbered


@pytest.mark.asyncio
async def test_sync_skips_unknown_and_custom_licenses(db_session: AsyncSession) -> None:
    from models import Obligation
    from services.obligation_service import sync_catalog_obligations

    _project, scan = await _project_with_scan(db_session)
    cv = await _make_component_version(db_session)
    # An ORT custom license (LicenseRef-*) is not in the catalog.
    custom = await _make_license(
        db_session, spdx_id=f"LicenseRef-x-{unique_suffix()}", category="unknown"
    )
    await _attach_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=custom.id)

    inserted = await sync_catalog_obligations(db_session, scan_id=scan.id)
    await db_session.commit()
    assert inserted == 0

    rows = (
        await db_session.execute(select(Obligation).where(Obligation.license_id == custom.id))
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_sync_no_findings_is_noop(db_session: AsyncSession) -> None:
    from services.obligation_service import sync_catalog_obligations

    _project, scan = await _project_with_scan(db_session)
    inserted = await sync_catalog_obligations(db_session, scan_id=scan.id)
    assert inserted == 0


@pytest.mark.asyncio
async def test_list_obligations_surfaces_catalog_after_scan(db_session: AsyncSession) -> None:
    """End-to-end: a project whose scan saw MIT + GPL surfaces their obligations
    through the public read service without any seeded obligation rows."""
    from services.obligation_service import list_project_obligations
    from tests._helpers import principal_for

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    await db_session.commit()
    await db_session.refresh(project)

    cv = await _make_component_version(db_session)
    mit = await _make_license(db_session, spdx_id="MIT", category="allowed")
    await _attach_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=mit.id)

    actor = principal_for(user, team_ids=[team.id])
    items, distribution, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor
    )
    await db_session.commit()

    kinds = {item["kind"] for item in items}
    assert KIND_ATTRIBUTION in kinds
    assert KIND_NOTICE in kinds
    assert total >= 2
    assert distribution.get(KIND_ATTRIBUTION, 0) >= 1


@pytest.mark.asyncio
async def test_generate_notice_includes_catalog_obligations(db_session: AsyncSession) -> None:
    """The generated NOTICE body credits the license AND renders its obligations
    pulled from the catalog (not '(no obligations recorded)')."""
    from services.obligation_service import generate_notice
    from tests._helpers import principal_for

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    await db_session.commit()
    await db_session.refresh(project)

    cv = await _make_component_version(db_session)
    apache = await _make_license(db_session, spdx_id="Apache-2.0", category="allowed")
    await _attach_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=apache.id)

    actor = principal_for(user, team_ids=[team.id])
    result = await generate_notice(db_session, project_id=project.id, actor=actor, fmt="text")
    await db_session.commit()

    body = result["body"]
    assert "Apache-2.0" in body
    # Attribution obligation text rendered, not the empty placeholder.
    assert "no obligations recorded" not in body.lower()
    assert result["obligation_count"] >= 1
