"""A cancelled scan asks the engine worker to reap right away (security review on #522, M1)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
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
    ("engine", "kind", "reaped"),
    [
        ("bomlens", "source", True),
        ("inprocess", "source", False),
        ("bomlens", "container", False),
    ],
)
async def test_cancel_enqueues_the_reaper_only_for_engine_source_scans(
    db_session: AsyncSession,  # noqa: F811 - the imported fixture
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    kind: str,
    reaped: bool,
) -> None:
    from services.admin_scan_service import cancel_scan

    monkeypatch.setenv("SCAN_ENGINE", engine)
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
