# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Finding ids and transition approvals across a re-persist (D1, real Postgres).

Three paths used to DELETE a scan's findings and insert them again: the
6-hourly rematch beat, a redelivered source scan, and a redelivered container
scan. ``transition_approvals.finding_id`` is ``ON DELETE CASCADE``, so every
pending and decided two-person approval on the scan disappeared without a
trace, and every finding id changed every six hours, breaking the links that
notifications and tickets carry.

Each test here drives the real task end to end (only the scanner binaries are
stubbed) with captured Trivy output that has several CVEs per package
(``tests/fixtures/trivy/PROVENANCE.md``), and checks the sequence rather than a
single call:

* (a) approval requested, then rematch: approval and finding id survive.
* (b) approval decided, then the same scan re-runs: the record survives.
* (c) a pair the new report no longer has is deleted, and its approval goes
  with it. That is intended: the approval authorised a change to a finding the
  scan no longer claims. The other approvals and ids are untouched.
* (d) the container path, (a) and (c) together.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from integrations.cdxgen import CdxgenResult
from integrations.trivy import TrivyResult
from models import (
    AuditLog,
    ComponentVersion,
    Scan,
    TransitionApproval,
    Vulnerability,
    VulnerabilityFinding,
)
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "trivy"
_SOURCE_REPORT = _FIXTURES / "npm-lockfile-source-report.json"
_IMAGE_REPORT = _FIXTURES / "alpine-3.19-image-report.json"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _load(path: Path) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return report


def _pairs(report: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (v["VulnerabilityID"], v["PkgName"], v["InstalledVersion"])
        for r in report["Results"]
        for v in r.get("Vulnerabilities") or []
    }


def _without_package(report: dict[str, Any], pkg_name: str) -> dict[str, Any]:
    """The same captured report with one package's vulnerabilities gone."""
    trimmed = copy.deepcopy(report)
    for result in trimmed["Results"]:
        vulns = result.get("Vulnerabilities") or []
        result["Vulnerabilities"] = [v for v in vulns if v["PkgName"] != pkg_name]
    return trimmed


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _seed_scan(
    *, kind: str, scan_metadata: dict[str, Any]
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Org, team, one user and one queued scan. Returns (scan, team, user)."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _build() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team, git_url=None)
            scan = Scan(
                project_id=project.id,
                kind=kind,
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata=scan_metadata,
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            ids = (scan.id, team.id, user.id)
        await engine.dispose()
        return ids

    return asyncio.run(_build())


def _findings(session: Session, scan_id: uuid.UUID) -> dict[tuple[str, str], uuid.UUID]:
    """``(CVE id, purl_with_version) -> finding id`` for one scan."""
    session.expire_all()
    rows = session.execute(
        select(
            Vulnerability.external_id,
            ComponentVersion.purl_with_version,
            VulnerabilityFinding.id,
        )
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == VulnerabilityFinding.component_version_id,
        )
        .where(VulnerabilityFinding.scan_id == scan_id)
    ).all()
    return {(cve, purl): fid for cve, purl, fid in rows}


def _request_approval(
    session: Session,
    *,
    finding_id: uuid.UUID,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    decided: bool = False,
) -> uuid.UUID:
    approval = TransitionApproval(
        finding_id=finding_id,
        team_id=team_id,
        target_status="suppressed",
        justification="Not reachable from our build; agreed with security.",
        requested_by_user_id=user_id,
        state="pending",
    )
    if decided:
        approval.state = "approved"
        approval.decided_by_user_id = user_id
        approval.decision_note = "Agreed."
        approval.decided_at = datetime.now(UTC)
    session.add(approval)
    session.commit()
    return approval.id


def _approval(session: Session, approval_id: uuid.UUID) -> TransitionApproval | None:
    session.expire_all()
    return session.get(TransitionApproval, approval_id)


# ---------------------------------------------------------------------------
# Source pipeline harness: cdxgen and Trivy stubbed, everything else real
# ---------------------------------------------------------------------------


def _sbom_for(report: dict[str, Any]) -> dict[str, Any]:
    packages = sorted({(name, version) for _, name, version in _pairs(report)})
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": "d1-app", "version": "0"}},
        "components": [
            {
                "type": "library",
                "bom-ref": f"pkg:npm/{name}@{version}",
                "name": name,
                "version": version,
                "purl": f"pkg:npm/{name}@{version}",
            }
            for name, version in packages
        ],
    }


def _stub_source_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    current: Callable[[], dict[str, Any]],
) -> None:
    """Point the source task at a workspace and stub the two scanner binaries.

    ``current`` returns the Trivy report to hand back, so a test can change
    what the next run reports. The SBOM always lists every fixture package.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    full = _load(_SOURCE_REPORT)

    def _cdxgen(*, source_dir: Path, output_dir: Path, **_kwargs: object) -> CdxgenResult:  # noqa: ARG001
        sbom = _sbom_for(full)
        out = output_dir.parent / "cdxgen"
        out.mkdir(parents=True, exist_ok=True)
        sbom_path = out / "cdxgen.cdx.json"
        sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        return CdxgenResult(sbom_path=sbom_path, sbom=sbom)

    def _trivy(
        sbom_path: Path | None = None,  # noqa: ARG001
        output_dir: Path | None = None,
        **_kwargs: object,
    ) -> TrivyResult:
        assert output_dir is not None
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-sbom.json"
        report = current()
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.cdxgen_adapter.run_cdxgen", _cdxgen)
    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _trivy)
    monkeypatch.setattr("tasks.vulnerability_rematch.run_trivy_sbom", _trivy)

    from tasks.notify import send_notification_task

    monkeypatch.setattr(send_notification_task, "delay", lambda *a, **k: None)


def _run_source(scan_id: uuid.UUID) -> None:
    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback


def _status(session: Session, scan_id: uuid.UUID) -> str:
    session.expire_all()
    return session.execute(select(Scan.status).where(Scan.id == scan_id)).scalar_one()


def _redeliver(session: Session, scan_id: uuid.UUID) -> None:
    """Put the scan back where a Celery redelivery finds it: running."""
    scan = session.get(Scan, scan_id)
    assert scan is not None
    scan.status = "running"
    session.commit()


def _axios_key(findings: dict[tuple[str, str], uuid.UUID]) -> tuple[str, str]:
    # axios carries 25 CVEs in the captured report: the dense case.
    return sorted(k for k in findings if k[1] == "pkg:npm/axios@0.21.0")[0]


# ---------------------------------------------------------------------------
# (a) rematch
# ---------------------------------------------------------------------------


def test_rematch_keeps_finding_ids_and_a_pending_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    report = _load(_SOURCE_REPORT)
    _stub_source_pipeline(monkeypatch, tmp_path, lambda: report)
    scan_id, team_id, user_id = _seed_scan(kind="source", scan_metadata={})
    _run_source(scan_id)
    assert _status(sync_session, scan_id) == "succeeded"

    before = _findings(sync_session, scan_id)
    assert len(before) == len(_pairs(report)) == 34
    target = _axios_key(before)
    approval_id = _request_approval(
        sync_session, finding_id=before[target], team_id=team_id, user_id=user_id
    )

    from tasks.vulnerability_rematch import rematch_scan_findings

    summary = rematch_scan_findings.run(str(scan_id))
    assert summary["skipped"] is False, summary
    assert summary["current_count"] == 34, summary

    # The approval first: checked after the ids, a lost approval would only
    # ever be reported as changed ids.
    approval = _approval(sync_session, approval_id)
    assert approval is not None, "rematch deleted a pending transition approval"
    assert approval.state == "pending"
    assert approval.finding_id == before[target]
    after = _findings(sync_session, scan_id)
    assert after == before, "rematch changed finding ids"


# ---------------------------------------------------------------------------
# (b) re-run of the same scan
# ---------------------------------------------------------------------------


def test_rerun_of_the_same_scan_keeps_a_decided_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    report = _load(_SOURCE_REPORT)
    _stub_source_pipeline(monkeypatch, tmp_path, lambda: report)
    scan_id, team_id, user_id = _seed_scan(kind="source", scan_metadata={})
    _run_source(scan_id)
    before = _findings(sync_session, scan_id)
    target = _axios_key(before)
    approval_id = _request_approval(
        sync_session,
        finding_id=before[target],
        team_id=team_id,
        user_id=user_id,
        decided=True,
    )

    _redeliver(sync_session, scan_id)
    _run_source(scan_id)
    assert _status(sync_session, scan_id) == "succeeded"

    approval = _approval(sync_session, approval_id)
    assert approval is not None, "the re-run deleted a decided transition approval"
    assert approval.state == "approved"
    assert approval.decided_by_user_id == user_id
    assert approval.finding_id == before[target]
    after = _findings(sync_session, scan_id)
    assert after == before, "the re-run changed finding ids"


# ---------------------------------------------------------------------------
# (c) a pair that genuinely disappears
# ---------------------------------------------------------------------------


def test_a_vanished_pair_is_deleted_with_its_approval_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
    generic_audit_listener: bool,
) -> None:
    """Intended: the approval goes with the finding it was about.

    minimist is patched in the next report. Its two findings must go (a
    finding the scan no longer claims would stay in the build gate's count),
    and so does the approval on one of them; the approval on an axios finding
    and every other id must not move.
    """
    full = _load(_SOURCE_REPORT)
    state = {"report": full}
    _stub_source_pipeline(monkeypatch, tmp_path, lambda: state["report"])
    scan_id, team_id, user_id = _seed_scan(kind="source", scan_metadata={})
    _run_source(scan_id)
    before = _findings(sync_session, scan_id)

    minimist = sorted(k for k in before if k[1] == "pkg:npm/minimist@1.2.0")
    assert len(minimist) == 2
    gone_approval = _request_approval(
        sync_session, finding_id=before[minimist[0]], team_id=team_id, user_id=user_id
    )
    kept_target = _axios_key(before)
    kept_approval = _request_approval(
        sync_session, finding_id=before[kept_target], team_id=team_id, user_id=user_id
    )

    state["report"] = _without_package(full, "minimist")
    from tasks.vulnerability_rematch import rematch_scan_findings

    summary = rematch_scan_findings.run(str(scan_id))
    assert summary["skipped"] is False, summary

    kept = _approval(sync_session, kept_approval)
    assert kept is not None, "an approval on a finding still reported was deleted"
    assert kept.finding_id == before[kept_target]
    assert _approval(sync_session, gone_approval) is None
    after = _findings(sync_session, scan_id)
    expected = {k: v for k, v in before.items() if k not in minimist}
    assert after == expected

    # The deletion is on the record, by the system actor, naming the approval
    # the cascade took with it.
    rows = _audits(sync_session, action="delete", ids=[before[k] for k in minimist])
    recorded = sorted(
        (
            row.target_id or "",
            row.actor_user_id,
            (row.diff or {}).get("transition_approval_ids"),
        )
        for row in rows
    )
    expected_rows = sorted(
        [
            (str(before[minimist[0]]), None, [str(gone_approval)]),
            (str(before[minimist[1]]), None, []),
        ]
    )
    assert recorded == expected_rows, f"delete audit rows: {recorded!r}"
    assert _has_generic_rows(
        sync_session, action="delete", ids=[before[k] for k in minimist]
    ) is generic_audit_listener


# The reconciler's own rows carry ``diff.reason``. In a process where the API's
# generic ``before_flush`` listener is installed (it registers on the global
# Session class, so any earlier API test in the same pytest process installs
# it), that listener writes its own row for the same flush, without a reason.
# Production workers never install it. Both cases are exercised through the
# ``generic_audit_listener`` parameter below.
_RECONCILER_REASONS = {"no_longer_reported", "carried_forward"}


@pytest.fixture(params=[False, True], ids=["worker-only", "with-api-listener"])
def generic_audit_listener(request: pytest.FixtureRequest) -> Iterator[bool]:
    from sqlalchemy import event

    from core.audit import _after_flush, _after_soft_rollback, _before_flush

    hooks = [
        ("before_flush", _before_flush),
        ("after_flush", _after_flush),
        ("after_soft_rollback", _after_soft_rollback),
    ]
    was_installed = {name: event.contains(Session, name, fn) for name, fn in hooks}
    for name, fn in hooks:
        if request.param and not was_installed[name]:
            event.listen(Session, name, fn)
        if not request.param and was_installed[name]:
            event.remove(Session, name, fn)
    try:
        yield bool(request.param)
    finally:
        for name, fn in hooks:
            now = event.contains(Session, name, fn)
            if was_installed[name] and not now:
                event.listen(Session, name, fn)
            if not was_installed[name] and now:
                event.remove(Session, name, fn)


def _has_generic_rows(session: Session, *, action: str, ids: list[uuid.UUID]) -> bool:
    """Whether the API's generic listener also wrote rows (proves the mode applied)."""
    session.expire_all()
    rows = session.execute(
        select(AuditLog).where(
            AuditLog.target_table == "vulnerability_findings",
            AuditLog.action == action,
            AuditLog.target_id.in_([str(i) for i in ids]),
        )
    ).scalars()
    return any((row.diff or {}).get("reason") not in _RECONCILER_REASONS for row in rows)


def _audits(session: Session, *, action: str, ids: list[uuid.UUID]) -> list[AuditLog]:
    """The reconciler's audit rows for these findings (see the note above)."""
    session.expire_all()
    return [
        row
        for row in session.execute(
            select(AuditLog).where(
                AuditLog.target_table == "vulnerability_findings",
                AuditLog.action == action,
                AuditLog.target_id.in_([str(i) for i in ids]),
            )
        ).scalars()
        if (row.diff or {}).get("reason") in _RECONCILER_REASONS
    ]


# ---------------------------------------------------------------------------
# A pass that could not read the whole report deletes nothing
# ---------------------------------------------------------------------------


def test_a_catalog_race_on_one_entry_does_not_delete_its_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """An entry skipped for a transient reason is not a vanished pair.

    The catalog row for one CVE is lost to a race during the rematch. Its
    finding, and the agreed suppression on it, must still be there afterwards.
    """
    report = _load(_SOURCE_REPORT)
    _stub_source_pipeline(monkeypatch, tmp_path, lambda: report)
    scan_id, team_id, user_id = _seed_scan(kind="source", scan_metadata={})
    _run_source(scan_id)
    before = _findings(sync_session, scan_id)
    target = _axios_key(before)
    approval_id = _request_approval(
        sync_session, finding_id=before[target], team_id=team_id, user_id=user_id, decided=True
    )

    import services.vulnerability_matching as matching

    real_upsert = matching._upsert_vulnerability_from_trivy

    def _lose_the_race(session: Session, *, external_id: str, **kwargs: Any) -> Any:
        if external_id == target[0]:
            return None, True
        return real_upsert(session, external_id=external_id, **kwargs)

    monkeypatch.setattr(matching, "_upsert_vulnerability_from_trivy", _lose_the_race)
    from tasks.vulnerability_rematch import rematch_scan_findings

    summary = rematch_scan_findings.run(str(scan_id))
    assert summary["skipped"] is False, summary

    assert _approval(sync_session, approval_id) is not None, "a catalog race deleted an approval"
    assert _findings(sync_session, scan_id) == before


def test_a_report_without_results_deletes_nothing_but_an_empty_list_does(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """Missing ``Results`` is malformed; ``Results: []`` is a clean result.

    Driven through a re-run of the same source scan, which has no guard of its
    own in front of the persister (the rematch task does).
    """
    full = _load(_SOURCE_REPORT)
    state: dict[str, dict[str, Any]] = {"report": full}
    _stub_source_pipeline(monkeypatch, tmp_path, lambda: state["report"])
    scan_id, team_id, user_id = _seed_scan(kind="source", scan_metadata={})
    _run_source(scan_id)
    before = _findings(sync_session, scan_id)
    approval_id = _request_approval(
        sync_session, finding_id=before[_axios_key(before)], team_id=team_id, user_id=user_id
    )

    state["report"] = {"SchemaVersion": 2, "ArtifactType": "cyclonedx"}
    _redeliver(sync_session, scan_id)
    _run_source(scan_id)
    assert _approval(sync_session, approval_id) is not None, "a malformed report deleted it"
    assert _findings(sync_session, scan_id) == before

    state["report"] = {"SchemaVersion": 2, "ArtifactType": "cyclonedx", "Results": []}
    _redeliver(sync_session, scan_id)
    _run_source(scan_id)
    assert _findings(sync_session, scan_id) == {}
    assert _approval(sync_session, approval_id) is None


def test_a_carried_status_on_a_kept_row_is_audited(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
    generic_audit_listener: bool,
) -> None:
    """A kept ``new`` row that takes the project's verdict gets an update row.

    Two scans of one project; the analyst rules on the first scan's finding,
    and the rematch of the second scan carries that verdict onto its own row.
    The finding's status history reads ``diff.status`` from these rows.
    """
    report = _load(_SOURCE_REPORT)
    _stub_source_pipeline(monkeypatch, tmp_path, lambda: report)
    first_scan, _team_id, user_id = _seed_scan(kind="source", scan_metadata={})
    _run_source(first_scan)
    first = sync_session.get(Scan, first_scan)
    assert first is not None
    second = Scan(
        project_id=first.project_id,
        kind="source",
        status="queued",
        progress_percent=0,
        requested_by_user_id=user_id,
        scan_metadata={},
    )
    sync_session.add(second)
    sync_session.commit()
    second_scan = second.id
    _run_source(second_scan)

    key = _axios_key(_findings(sync_session, first_scan))
    ruled = sync_session.get(VulnerabilityFinding, _findings(sync_session, first_scan)[key])
    assert ruled is not None
    ruled.status = "not_affected"
    ruled.analysis_state = "not_affected"
    ruled.analysis_justification = "code_not_reachable"
    ruled.analyst_user_id = user_id
    ruled.analyzed_at = datetime.now(UTC)
    sync_session.commit()

    kept_id = _findings(sync_session, second_scan)[key]
    from tasks.vulnerability_rematch import rematch_scan_findings

    assert rematch_scan_findings.run(str(second_scan))["skipped"] is False
    sync_session.expire_all()
    kept = sync_session.get(VulnerabilityFinding, kept_id)
    assert kept is not None and kept.status == "not_affected"
    updates = _audits(sync_session, action="update", ids=[kept_id])
    seen = sorted(
        (
            str(u.actor_user_id),
            str((u.diff or {}).get("status")),
            str((u.diff or {}).get("previous_status")),
        )
        for u in updates
    )
    assert seen == [("None", "not_affected", "new")], f"carry audit rows: {seen!r}"
    assert _has_generic_rows(sync_session, action="update", ids=[kept_id]) is generic_audit_listener


def test_a_container_report_without_results_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    full = _load(_IMAGE_REPORT)
    state: dict[str, dict[str, Any]] = {"report": full}
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)

    def _trivy_image(*, image_ref: str, output_dir: Path, **_kwargs: object) -> TrivyResult:  # noqa: ARG001
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy.json"
        report_path.write_text(json.dumps(state["report"]), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=state["report"])

    monkeypatch.setattr("tasks.scan_container.trivy_adapter.run_trivy_image", _trivy_image)
    from tasks.scan_container import scan_container_task

    scan_id, team_id, user_id = _seed_scan(
        kind="container", scan_metadata={"image_ref": "alpine:3.19"}
    )
    assert scan_container_task.apply(args=[str(scan_id)]).successful()
    before = _findings(sync_session, scan_id)
    approval_id = _request_approval(
        sync_session, finding_id=next(iter(before.values())), team_id=team_id, user_id=user_id
    )

    state["report"] = {"SchemaVersion": 2, "ArtifactName": "alpine:3.19"}
    _redeliver(sync_session, scan_id)
    assert scan_container_task.apply(args=[str(scan_id)]).successful()
    assert _approval(sync_session, approval_id) is not None, "a malformed report deleted it"
    assert _findings(sync_session, scan_id) == before


# ---------------------------------------------------------------------------
# (d) container path
# ---------------------------------------------------------------------------


def test_container_rerun_keeps_ids_and_approval_and_drops_a_vanished_pair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    full = _load(_IMAGE_REPORT)
    state = {"report": full}
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)

    def _trivy_image(*, image_ref: str, output_dir: Path, **_kwargs: object) -> TrivyResult:  # noqa: ARG001
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy.json"
        report_path.write_text(json.dumps(state["report"]), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=state["report"])

    monkeypatch.setattr("tasks.scan_container.trivy_adapter.run_trivy_image", _trivy_image)

    from tasks.scan_container import scan_container_task

    scan_id, team_id, user_id = _seed_scan(
        kind="container", scan_metadata={"image_ref": "alpine:3.19"}
    )
    assert scan_container_task.apply(args=[str(scan_id)]).successful()
    assert _status(sync_session, scan_id) == "succeeded"
    before = _findings(sync_session, scan_id)
    assert len(before) == len(_pairs(full)) == 10

    by_pkg: dict[str, list[tuple[str, str]]] = {}
    for key in before:
        by_pkg.setdefault(key[1].split("@")[0], []).append(key)
    busybox = sorted(by_pkg["pkg:apk/alpine/busybox"])
    musl = sorted(by_pkg["pkg:apk/alpine/musl"])
    kept_approval = _request_approval(
        sync_session, finding_id=before[busybox[0]], team_id=team_id, user_id=user_id
    )
    gone_approval = _request_approval(
        sync_session,
        finding_id=before[musl[0]],
        team_id=team_id,
        user_id=user_id,
        decided=True,
    )

    # Same report, redelivered: nothing may move.
    _redeliver(sync_session, scan_id)
    assert scan_container_task.apply(args=[str(scan_id)]).successful()
    assert _status(sync_session, scan_id) == "succeeded"
    assert _approval(sync_session, kept_approval) is not None, "re-run deleted an approval"
    assert _approval(sync_session, gone_approval) is not None, "re-run deleted an approval"
    assert _findings(sync_session, scan_id) == before, "re-run changed finding ids"

    # musl patched: its pairs and their approval go, the rest stays.
    state["report"] = _without_package(full, "musl")
    _redeliver(sync_session, scan_id)
    assert scan_container_task.apply(args=[str(scan_id)]).successful()
    assert _approval(sync_session, kept_approval) is not None
    assert _approval(sync_session, gone_approval) is None
    after = _findings(sync_session, scan_id)
    assert after == {k: v for k, v in before.items() if k not in set(musl)}
