"""
The single super-admin reach decision in ``core.authz``.

The integration file ``tests/integration/test_demo_super_admin_reach.py`` runs
every affected service against Postgres. This file pins the helpers' own
contract without a database: what each returns off and on the public demo,
and that a normal deployment's super admin gets exactly the unfiltered query
it got before (``None`` from the list helper, no clause added).
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.authz import (
    group_visibility_filter,
    is_super_admin,
    super_admin_group_filter,
    super_admin_reaches,
    team_scope_filter,
)
from core.security import CurrentUser
from models import APIKey


def _actor(*, is_superuser: bool = False, role: str = "developer") -> CurrentUser:
    team = uuid.uuid4()
    return CurrentUser(
        id=uuid.uuid4(),
        email="someone@example.com",
        role=role,
        team_ids=[team],
        team_roles={team: role},
        is_superuser=is_superuser,
    )


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch):
    def _set(on: bool) -> None:
        if on:
            monkeypatch.setenv("DEMO_READ_ONLY", "true")
        else:
            monkeypatch.delenv("DEMO_READ_ONLY", raising=False)

    return _set


@pytest.mark.parametrize(
    ("is_superuser", "role", "expected"),
    [
        (True, "super_admin", True),
        (False, "super_admin", True),  # derived role alone, as every service honoured
        (True, "developer", True),
        (False, "group_admin", False),
    ],
)
def test_identity(is_superuser: bool, role: str, expected: bool) -> None:
    assert is_super_admin(_actor(is_superuser=is_superuser, role=role)) is expected


@pytest.mark.parametrize("group_id", [None, uuid.uuid4()])
async def test_normal_deployment_reaches_everything(demo, group_id) -> None:
    demo(False)
    session = AsyncMock()
    assert await super_admin_reaches(
        session, _actor(is_superuser=True, role="super_admin"), group_id
    )
    session.execute.assert_not_called()


async def test_non_super_admin_never_reaches(demo) -> None:
    for on in (False, True):
        demo(on)
        assert not await super_admin_reaches(AsyncMock(), _actor(role="group_admin"), uuid.uuid4())


async def test_demo_denies_a_target_less_bypass(demo) -> None:
    demo(True)
    session = AsyncMock()
    assert not await super_admin_reaches(
        session, _actor(is_superuser=True, role="super_admin"), None
    )
    session.execute.assert_not_called()


@pytest.mark.parametrize("in_demo_org", [True, False])
async def test_demo_judges_a_group_by_its_organisation(demo, monkeypatch, in_demo_org) -> None:
    from services import demo_scope

    demo(True)
    probe = AsyncMock(return_value=in_demo_org)
    monkeypatch.setattr(demo_scope, "is_demo_group", probe)
    group_id = uuid.uuid4()
    session = AsyncMock()
    reached = await super_admin_reaches(
        session, _actor(is_superuser=True, role="super_admin"), group_id
    )
    assert reached is in_demo_org
    probe.assert_awaited_once_with(session, group_id)


def test_list_filters_on_a_normal_deployment(demo) -> None:
    demo(False)
    admin = _actor(is_superuser=True, role="super_admin")
    # No clause at all: the list runs the query it always ran.
    assert group_visibility_filter(admin, APIKey.team_id) is None
    assert str(super_admin_group_filter(admin, APIKey.team_id)) == "true"
    # Not a super admin: the membership predicate, unconditionally.
    assert super_admin_group_filter(_actor(), APIKey.team_id) is None
    assert group_visibility_filter(_actor(), APIKey.team_id) is not None


def test_list_filters_on_the_demo(demo) -> None:
    demo(True)
    admin = _actor(is_superuser=True, role="super_admin")
    clause = group_visibility_filter(admin, APIKey.team_id)
    assert clause is not None
    sql = str(clause.compile(compile_kwargs={"literal_binds": True}))
    # Bounded to the demo organisation, ORed with the actor's memberships.
    assert "demo-org" in sql
    assert " OR " in sql


def test_team_scope_filter_keeps_its_stricter_identity(demo) -> None:
    """A principal whose role reads super_admin without the flag is a member there.

    ``team_scope_filter`` grants the unbounded branch on ``is_superuser`` alone
    (security review H-2); the shared reach did not change that.
    """
    demo(False)
    role_only = _actor(is_superuser=False, role="super_admin")
    flagged = _actor(is_superuser=True, role="super_admin")
    assert str(team_scope_filter(flagged)) == "true"
    assert str(team_scope_filter(role_only)) != "true"


# ---------------------------------------------------------------------------
# Guard: nothing outside core.authz decides a super admin's bypass itself
# ---------------------------------------------------------------------------

_BACKEND = Path(__file__).resolve().parents[2]

#: Directories that are not application code: the suite itself, migrations
#: (frozen history), and anything a local toolchain drops in the tree.
_SKIP_DIRS = {"tests", "alembic", ".venv", "__pycache__"}

#: ``(path, enclosing function)`` pairs allowed to read the super-admin flag or
#: role directly, each for a reason that is not a bypass decision.
_ALLOWED: dict[tuple[str, str], str] = {
    # The admin URL space dependency (require_super_admin_or_404).
    ("core/security.py", "_check"): "admin-only routes; the demo narrows them by allow-list",
    # Token claims for the user's own login: identity, not reach.
    ("services/auth_service.py", "issue_token_pair"): "role claim in the user's own token",
    ("services/auth_service.py", "rotate_refresh"): "role claim in the user's own token",
    ("services/oauth_service.py", "_issue_token_pair_in_session"): "role claim, same",
    # Account management: the flag is the data being shown or edited.
    ("services/admin_user_service.py", "_effective_role"): "renders a user's role",
    ("services/admin_user_service.py", "update_user_role"): "edits the flag",
    ("services/admin_user_service.py", "deactivate_user"): "last-admin protection",
    ("scripts/create_super_admin.py", "_main"): "bootstrap script",
    ("scripts/seed_demo.py", "_collect_existing_summary"): "seed summary output",
    # The stricter identity (is_superuser alone, see team_scope_filter), used
    # after the reach was decided or to pick the shared reach.
    ("services/project_detail_service.py", "_resolve_team_scoped_role"): "role after the gate",
    ("services/group_directory_service.py", "_actor_visibility_predicate"): "shared reach",
    ("services/project_service.py", "list_projects"): "empty-membership early exit",
    ("services/scan_service.py", "list_scans_for_actor"): "empty-membership early exit",
    ("services/dashboard_service.py", "_accessible_project_ids"): "empty-membership early exit",
    ("api/v1/transition_approvals.py", "list_pending_endpoint"): "selects the shared reach",
}

_SUPER = "super_admin"


def _names_for_super_admin(tree: ast.AST) -> set[str]:
    """Module-level names bound to ``"super_admin"`` or a collection holding it."""
    names: set[str] = set()
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if _holds_super(node.value, set()):
                names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _holds_super(node: ast.AST, names: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return bool(node.value == _SUPER)
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return any(_holds_super(e, names) for e in node.elts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        # frozenset({...}) / set([...]) / tuple(...)
        return node.func.id in {"frozenset", "set", "tuple"} and any(
            _holds_super(a, names) for a in node.args
        )
    return False


def _is_attr_read(node: ast.AST, attr: str) -> bool:
    """``x.<attr>`` or ``getattr(x, "<attr>"[, default])``."""
    if isinstance(node, ast.Attribute):
        return node.attr == attr
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == attr
    )


def _flag_reads_in_test(test: ast.AST) -> list[ast.AST]:
    """``is_superuser`` reads that decide *test*, not ones nested in a call."""
    found: list[ast.AST] = []
    stack = [test]
    while stack:
        node = stack.pop()
        if _is_attr_read(node, "is_superuser"):
            found.append(node)
            continue
        if isinstance(node, ast.Call):
            continue  # an argument to a call (bool(), a query) is not the decision
        stack.extend(ast.iter_child_nodes(node))
    return found


def _shortcuts(source: str, rel: str) -> list[str]:
    """Super-admin decisions taken locally in *source* (a file at *rel*)."""
    tree = ast.parse(source)
    super_names = _names_for_super_admin(tree)
    found: list[str] = []

    def visit(node: ast.AST, func: str) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in {"_is_super_admin", "is_super_admin"}:
                found.append(f"{rel}:{node.lineno} def {node.name}")
            func = node.name
        allowed = (rel, func) in _ALLOWED
        if isinstance(node, ast.Compare) and not allowed:
            operands = [node.left, *node.comparators]
            role_side = any(_is_attr_read(o, "role") for o in operands)
            super_side = any(_holds_super(o, super_names) for o in operands)
            if role_side and super_side:
                found.append(f"{rel}:{node.lineno} role compared with super_admin")
        tests: list[ast.AST] = []
        if isinstance(node, ast.If | ast.IfExp | ast.While | ast.Assert):
            tests.append(node.test)
        elif isinstance(node, ast.Return) and node.value is not None:
            tests.append(node.value)
        if not allowed:
            for test in tests:
                for read in _flag_reads_in_test(test):
                    line = getattr(read, "lineno", 0)
                    found.append(f"{rel}:{line} is_superuser decides a branch")
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, "<module>")
    return found


def _backend_sources() -> list[Path]:
    paths = []
    for path in sorted(_BACKEND.rglob("*.py")):
        rel = path.relative_to(_BACKEND)
        if rel.parts[0] in _SKIP_DIRS or any(p in _SKIP_DIRS for p in rel.parts):
            continue
        if rel.as_posix() == "core/authz.py":
            continue
        paths.append(path)
    return paths


@pytest.mark.parametrize(
    "snippet",
    [
        "def _is_super_admin(a):\n    return True\n",
        'def f(a):\n    return a.role == "super_admin"\n',
        'def f(a):\n    return "super_admin" == a.role\n',
        'def f(a):\n    return a.role != "super_admin"\n',
        'def f(a):\n    return a.role in ("group_admin", "super_admin")\n',
        'def f(a):\n    return a.role in {"super_admin"}\n',
        'def f(a):\n    return a.role in frozenset({"super_admin"})\n',
        'ADMIN = "super_admin"\ndef f(a):\n    return a.role == ADMIN\n',
        'ADMINS = ("super_admin",)\ndef f(a):\n    return a.role in ADMINS\n',
        'def f(a):\n    return getattr(a, "role", None) == "super_admin"\n',
        "def f(a):\n    if a.is_superuser:\n        return 1\n",
        "def f(a):\n    return a.is_superuser\n",
        "def f(a):\n    return a.is_superuser or a.team_ids\n",
        "def f(a):\n    return 1 if a.is_superuser else 0\n",
        'def f(a):\n    if not getattr(a, "is_superuser", False):\n        return 0\n',
        "def f(a):\n    while a.is_superuser:\n        pass\n",
    ],
)
def test_the_detector_catches_each_shape(snippet: str) -> None:
    """Every shape the guard claims to look for, as a negative sample."""
    assert _shortcuts(snippet, "services/sample.py") != []


@pytest.mark.parametrize(
    "snippet",
    [
        # The flag passed through, not branched on.
        "def f(u):\n    return CurrentUser(is_superuser=bool(u.is_superuser))\n",
        "def f(q, User):\n    return q.where(User.is_superuser.is_(True))\n",
        # A role compared with something else.
        'def f(a):\n    return a.role == "group_admin"\n',
        # A parameter called role, not a principal's role attribute.
        'def f(role):\n    return role == "super_admin"\n',
    ],
)
def test_the_detector_lets_these_through(snippet: str) -> None:
    assert _shortcuts(snippet, "services/sample.py") == []


def test_the_detector_reads_the_real_tree() -> None:
    """The scan reaches the packages named, and authz itself would be flagged."""
    rels = {p.relative_to(_BACKEND).parts[0] for p in _backend_sources()}
    assert {"api", "core", "integrations", "notifications", "services", "tasks"} <= rels
    authz = _BACKEND / "core" / "authz.py"
    assert _shortcuts(authz.read_text(encoding="utf-8"), "core/authz.py")


def test_the_allow_list_has_no_stale_entries() -> None:
    """An entry nothing needs any more would silently widen the next change."""
    used: set[tuple[str, str]] = set()
    for path in _backend_sources():
        rel = path.relative_to(_BACKEND).as_posix()
        for entry in list(_ALLOWED):
            if entry[0] != rel:
                continue
            saved = _ALLOWED.pop(entry)
            try:
                if _shortcuts(path.read_text(encoding="utf-8"), rel):
                    used.add(entry)
            finally:
                _ALLOWED[entry] = saved
    assert set(_ALLOWED) - used == set()


def test_no_local_super_admin_shortcuts() -> None:
    offenders: list[str] = []
    for path in _backend_sources():
        rel = path.relative_to(_BACKEND).as_posix()
        offenders.extend(_shortcuts(path.read_text(encoding="utf-8"), rel))
    assert offenders == [], (
        "decide a super admin's bypass through core.authz (is_super_admin for "
        "identity after an access check, super_admin_reaches / "
        "super_admin_group_filter / group_visibility_filter for reach), or add "
        "an allow-list entry with the reason it is not a bypass"
    )
