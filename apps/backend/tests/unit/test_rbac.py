"""
Unit tests for RBAC dependencies — require_role / require_team_member.

We exercise the dependency callables directly with a mocked CurrentUser so
no database is needed. RBAC must:
- Allow super_admin everywhere
- Allow team_admin only inside their own team(s)
- Allow developer only inside their own team(s) and only on read endpoints
- Reject anonymous (None) callers everywhere
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from fastapi import HTTPException


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    role: str = "developer"
    team_ids: list[uuid.UUID] = field(default_factory=list)
    is_active: bool = True


def test_require_role_super_admin_allows_super_admin():
    from core.security import require_role

    dep = require_role("super_admin")
    user = _FakeUser(role="super_admin")
    # The dependency returns the user when allowed
    assert dep(current_user=user) is user


def test_require_role_super_admin_rejects_developer():
    from core.security import require_role

    dep = require_role("super_admin")
    user = _FakeUser(role="developer")
    with pytest.raises(HTTPException) as exc:
        dep(current_user=user)
    assert exc.value.status_code == 403


def test_require_role_team_admin_allows_team_admin_and_super_admin():
    from core.security import require_role

    dep = require_role("team_admin")
    assert dep(current_user=_FakeUser(role="team_admin"))
    assert dep(current_user=_FakeUser(role="super_admin"))


def test_require_role_rejects_anonymous():
    from core.security import require_role

    dep = require_role("developer")
    with pytest.raises(HTTPException) as exc:
        dep(current_user=None)
    assert exc.value.status_code == 401


def test_require_team_member_allows_member():
    from core.security import require_team_member

    team_id = uuid.uuid4()
    user = _FakeUser(role="developer", team_ids=[team_id])
    dep = require_team_member()
    assert dep(team_id=team_id, current_user=user) is user


def test_require_team_member_rejects_outsider():
    from core.security import require_team_member

    team_id = uuid.uuid4()
    other_team = uuid.uuid4()
    user = _FakeUser(role="developer", team_ids=[other_team])
    dep = require_team_member()
    with pytest.raises(HTTPException) as exc:
        dep(team_id=team_id, current_user=user)
    assert exc.value.status_code == 403


def test_require_team_member_super_admin_bypasses_team_check():
    from core.security import require_team_member

    team_id = uuid.uuid4()
    admin = _FakeUser(role="super_admin", team_ids=[])
    dep = require_team_member()
    assert dep(team_id=team_id, current_user=admin) is admin


def test_inactive_user_is_treated_as_anonymous():
    from core.security import require_role

    dep = require_role("developer")
    user = _FakeUser(role="developer", is_active=False)
    with pytest.raises(HTTPException) as exc:
        dep(current_user=user)
    assert exc.value.status_code == 401
