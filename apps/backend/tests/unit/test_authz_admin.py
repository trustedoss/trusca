"""
Unit tests for ``core.security.require_super_admin_or_404`` — Phase 4 PR #13.

The dependency factory implements the existence-hide contract:
  - Anonymous (None / no JWT)               -> 401
  - Inactive user                           -> 401
  - Authenticated, role != super_admin      -> 404
  - Authenticated super_admin (role)        -> pass-through
  - Authenticated super_admin (is_superuser) -> pass-through

We exercise the dependency callable directly with a mocked CurrentUser, no
DB or HTTP needed — matching the existing ``tests/unit/test_rbac.py``
pattern.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from fastapi import HTTPException


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str = "user@example.com"
    role: str = "developer"
    team_ids: list[uuid.UUID] = field(default_factory=list)
    team_roles: dict[uuid.UUID, str] = field(default_factory=dict)
    is_active: bool = True
    is_superuser: bool = False


# ---------------------------------------------------------------------------
# Anonymous + inactive
# ---------------------------------------------------------------------------


def test_require_super_admin_or_404_rejects_anonymous_with_401() -> None:
    from core.security import require_super_admin_or_404

    dep = require_super_admin_or_404()
    with pytest.raises(HTTPException) as exc:
        dep(current_user=None)
    assert exc.value.status_code == 401


def test_require_super_admin_or_404_rejects_inactive_user_with_401() -> None:
    """Inactive users behave like anonymous (their JWT may still verify)."""
    from core.security import require_super_admin_or_404

    dep = require_super_admin_or_404()
    user = _FakeUser(role="super_admin", is_superuser=True, is_active=False)
    with pytest.raises(HTTPException) as exc:
        dep(current_user=user)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Existence-hide: non-super-admin sees 404, NOT 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,is_superuser",
    [
        ("developer", False),
        ("team_admin", False),
    ],
)
def test_require_super_admin_or_404_returns_404_for_non_super_admin(
    role: str, is_superuser: bool
) -> None:
    from core.security import require_super_admin_or_404

    dep = require_super_admin_or_404()
    user = _FakeUser(role=role, is_superuser=is_superuser)
    with pytest.raises(HTTPException) as exc:
        dep(current_user=user)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Happy path: super_admin passes
# ---------------------------------------------------------------------------


def test_require_super_admin_or_404_passes_for_super_admin_role() -> None:
    from core.security import require_super_admin_or_404

    dep = require_super_admin_or_404()
    user = _FakeUser(role="super_admin", is_superuser=True)
    assert dep(current_user=user) is user


def test_require_super_admin_or_404_passes_for_is_superuser_flag() -> None:
    """The flag alone is not enough — both must agree (defense in depth).

    The loader sets role == 'super_admin' iff is_superuser. If a forged
    CurrentUser had only the flag set, the gate would still rely on role
    check passing through. We pin both must be set together.
    """
    from core.security import require_super_admin_or_404

    dep = require_super_admin_or_404()
    user = _FakeUser(role="super_admin", is_superuser=True)
    out = dep(current_user=user)
    assert out is user


def test_require_super_admin_or_404_passes_when_only_is_superuser_set() -> None:
    """If only is_superuser is True (no role), the gate still allows access.

    Defense-in-depth: an OR check between the flag and the role label means
    a CurrentUser produced by an alternate code path (e.g. a future SSO
    integration that sets is_superuser without populating role) still
    works. Neither path alone is forgeable from the wire — the JWT-only
    surface goes through ``_load_current_user``, which always sets both.
    """
    from core.security import require_super_admin_or_404

    dep = require_super_admin_or_404()
    user = _FakeUser(role="developer", is_superuser=True)
    out = dep(current_user=user)
    assert out is user
