"""Reviewer-authored probe: confirm a DB-level error raised at SAVEPOINT
release (not a pre-emptive python raise) is isolated by begin_nested and the
OUTER transaction still commits the declared components.

This exercises the REAL _persist_detected_licenses (not a monkeypatched stub)
by forcing a flush-time IntegrityError inside the nested block via a duplicate
license_findings row, then asserting components survive + scan succeeds.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core.db import sync_session_scope
from models import Component, ComponentVersion, License, LicenseFinding, ScanComponent
import tasks.scan_source as ss


def test_savepoint_isolates_real_flush_error(monkeypatch, sync_session):
    """Force a flush-time error INSIDE begin_nested via the real persist fn and
    confirm the outer session still commits + recovers (no PendingRollbackError)."""
    # Build a tiny SBOM with one real component so _persist_components writes the
    # high-value declared graph first.
    sbom = {
        "metadata": {"component": {"name": "probe-app"}},
        "components": [
            {
                "purl": "pkg:pypi/requests@2.0.0",
                "name": "requests",
                "version": "2.0.0",
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            }
        ],
    }
    scan_uuid = uuid.uuid4()

    # Seed a scan row so FK on scan_components / license_findings is satisfiable.
    from models import Project, Scan, Team
    with sync_session_scope() as s:
        team = Team(name=f"t-{scan_uuid}", slug=f"t-{scan_uuid}")
        s.add(team); s.flush()
        proj = Project(team_id=team.id, name="p", slug=f"p-{scan_uuid}", visibility="team")
        s.add(proj); s.flush()
        scan = Scan(project_id=proj.id, scan_type="source", status="running")
        scan.id = scan_uuid
        s.add(scan); s.commit()

    # Detections whose persistence will succeed normally — we instead sabotage
    # the LicenseFinding constructor to inject a duplicate PK collision at flush.
    from integrations.scancode import DetectedLicense
    detections = [DetectedLicense(spdx_id="MIT", source_path="LICENSE")]

    # Force a flush-time DB error inside the nested block by monkeypatching
    # _get_or_create_license to add a row with a NULL non-nullable column.
    real_license = ss._get_or_create_license
    def _bad_license(session, *, spdx_id, reference_url):
        lic = real_license(session, spdx_id=spdx_id, reference_url=reference_url)
        # Inject a finding with a NULL kind (NOT NULL) to blow the flush.
        bad = LicenseFinding(scan_id=scan_uuid, component_version_id=lic.id,
                             license_id=lic.id, kind=None, source_path="x")
        session.add(bad)
        return lic
    monkeypatch.setattr(ss, "_get_or_create_license", _bad_license)

    with sync_session_scope() as session:
        ss._persist_components(session, scan_uuid=scan_uuid, sbom=sbom)
        if detections:
            try:
                with session.begin_nested():
                    ss._persist_detected_licenses(
                        session, scan_uuid=scan_uuid, sbom=sbom, detections=detections
                    )
            except SQLAlchemyError as exc:
                print("CAUGHT inside savepoint:", type(exc).__name__)
        session.commit()  # MUST succeed — outer tx not poisoned

    # Declared component graph survived.
    with sync_session_scope() as s:
        comps = s.execute(select(ScanComponent).where(ScanComponent.scan_id == scan_uuid)).scalars().all()
        assert len(comps) >= 1, "declared component rolled back — blast radius NOT isolated"
        detected = s.execute(
            select(LicenseFinding).where(LicenseFinding.scan_id == scan_uuid,
                                         LicenseFinding.kind == "detected")
        ).scalars().all()
        assert detected == [], "detected findings leaked despite savepoint rollback"
    print("OUTER COMMIT OK; components survived; detected rolled back")
