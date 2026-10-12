"""A cancelled scan asks the engine worker to reap right away (security review on #522, M1)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    principal_loaded_from_db,
)
from tests.unit.services.test_admin_scan_service import (  # noqa: F401 - fixtures
    _FakeCeleryApp,
    _migrate_once,
    db_session,
)

pytestmark = pytest.mark.integration


class _RecordingCelery(_FakeCeleryApp):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    def send_task(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.sent.append(name)


@pytest.mark.parametrize(
    ("engine", "container_engine", "kind", "reaped"),
    [
        ("bomlens", "inprocess", "source", True),
        ("inprocess", "inprocess", "source", False),
        ("inprocess", "bomlens", "source", False),
        ("bomlens", "inprocess", "container", False),
        ("inprocess", "bomlens", "container", True),
        ("inprocess", "inprocess", "container", False),
    ],
)
async def test_cancel_enqueues_the_reaper_only_for_engine_scans(
    db_session: AsyncSession,  # noqa: F811 - the imported fixture
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    container_engine: str,
    kind: str,
    reaped: bool,
) -> None:
    """Each kind follows its own engine setting: SCAN_ENGINE for source,
    SCAN_CONTAINER_ENGINE for container."""
    from services.admin_scan_service import cancel_scan

    monkeypatch.setenv("SCAN_ENGINE", engine)
    monkeypatch.setenv("SCAN_CONTAINER_ENGINE", container_engine)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="running", kind=kind)
    scan.celery_task_id = "celery-task-xyz"
    await db_session.commit()

    celery = _RecordingCelery()
    await cancel_scan(db_session, actor=actor, scan_id=scan.id, celery_app_override=celery)

    assert celery.control.calls, "the task is still revoked"
    assert celery.sent == (["trustedoss.bomlens_engine_reaper"] if reaped else [])


@pytest.mark.parametrize("member", [True, False], ids=["member", "other-team"])
@pytest.mark.parametrize("status", ["running", "queued", "cancelled", "failed"])
async def test_user_cancel_of_a_container_engine_scan_by_access_and_state(
    db_session: AsyncSession,  # noqa: F811 - the imported fixture
    monkeypatch: pytest.MonkeyPatch,
    member: bool,
    status: str,
) -> None:
    """Hardening rule 1 on the cancel path of a SCAN_CONTAINER_ENGINE=bomlens
    scan: another team's scan is a 404 whatever its state (never the 409 that
    would confirm it exists), an ended one is a 409 for a member, and the
    reaper is asked for only when a cancel actually happened."""
    from services.admin_scan_service import (
        AdminScanNotFound,
        ScanAlreadyCancelled,
        cancel_scan_for_actor,
    )

    monkeypatch.setenv("SCAN_ENGINE", "inprocess")
    monkeypatch.setenv("SCAN_CONTAINER_ENGINE", "bomlens")
    org = await make_organization(db_session)
    owning = await make_team(db_session, organization=org)
    other = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=owning if member else other)
    actor = await principal_loaded_from_db(db_session, user=user)
    project = await make_project(db_session, team=owning)
    scan = await make_scan(
        db_session,
        project=project,
        status=status,
        kind="container",
        scan_metadata={"image_ref": "alpine:3.19.0"},
    )
    scan.celery_task_id = "celery-task-xyz"
    await db_session.commit()

    celery = _RecordingCelery()
    expected_error: type[Exception] | None = None
    if not member:
        expected_error = AdminScanNotFound
    elif status in ("cancelled", "failed"):
        expected_error = ScanAlreadyCancelled

    if expected_error is None:
        item = await cancel_scan_for_actor(
            db_session, actor=actor, scan_id=scan.id, celery_app_override=celery
        )
        assert item.status == "cancelled"
        assert celery.sent == ["trustedoss.bomlens_engine_reaper"]
    else:
        with pytest.raises(expected_error):
            await cancel_scan_for_actor(
                db_session, actor=actor, scan_id=scan.id, celery_app_override=celery
            )
        assert celery.sent == []
        assert celery.control.calls == []
        await db_session.refresh(scan)
        assert scan.status == status
