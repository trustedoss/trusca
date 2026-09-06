# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Privilege order is written down once, no matter how the copy is built.

``test_role_priority_has_one_home.py`` already guards ``_ROLE_PRIORITY``
having exactly one home, but it does that by walking the AST for
``ast.Dict`` literals. That is a guard on syntax, and syntax is one of many
ways to produce a dict: ``{"viewer": 1, "developer": 2}`` and
``dict(zip(("viewer", "developer"), (1, 2)))`` are two spellings of the same
object, and only the first one is a literal the AST guard can see. A copy
built with a dict comprehension, assembled at runtime by a helper function,
or expressed as ``list[tuple[str, int]]`` or an ``enum.IntEnum`` never
becomes an ``ast.Dict`` node, so the existing guard is silent about all of
them (issue #387).

This file adds a second guard that judges the *value* every consumer module
ends up holding after import, not the expression that produced it. A
duplicate is a duplicate because of what it contains (two or more role
names, all belonging to the same closed set), regardless of the statement
that built it. The two guards are kept independent on purpose (CLAUDE.md
hardening rule #8): this file does not import anything from
``test_role_priority_has_one_home.py``, and its own tests (below) confirm
each guard catches what the other misses rather than assuming it from the
fact that both are green.

``core.security._ROLE_PRIORITY`` itself is exempted by object identity, not
by module path or variable name: a second dict living inside
``core/security.py`` under a different name, holding the same values, is
still a copy and still fails. Identity is also why a module that legitimately
imports and re-exports the real object (``from core.security import
_ROLE_PRIORITY``) is not flagged: it holds a reference to the one object,
not a value that merely looks the same.
"""

from __future__ import annotations

import enum
import importlib
import importlib.util
import pkgutil
import textwrap
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]

SEARCH_DIRS = ("api", "core", "services", "tasks", "integrations", "notifications", "schemas")

#: Mirrors the AST guard's threshold and rationale: one name is a lookup, not
#: a ranking. Kept as its own constant in this file rather than imported from
#: the AST guard module, so the two guards stay independent implementations
#: of the same rule instead of one guard calling into the other's internals.
_MIN_GRADES_TO_COUNT = 2

#: Modules that must be skipped when walking SEARCH_DIRS, each with a reason.
#: A 2026-09-07 sweep imported all 348 modules under SEARCH_DIRS with no
#: DATABASE_URL, REDIS_URL, or SECRET_KEY set at all (every accessor in
#: core.config reads os.getenv() lazily per CLAUDE.md core rule #11, so
#: nothing connects at import time). If a future module grows an
#: import-time side effect that needs a live dependency, add it here with a
#: reason rather than letting an ImportError take the whole guard down
#: silently (the guard would stop partway through SEARCH_DIRS and every
#: module after the failure would go unchecked, which looks identical to a
#: passing run).
SKIPPED_MODULES: dict[str, str] = {}


def _role_names() -> frozenset[str]:
    from models.auth import ROLE_VALUES

    return frozenset(ROLE_VALUES)


def _is_role_keyed_mapping(value: object, roles: frozenset[str]) -> bool:
    """True for any ``Mapping`` (dict literal, comprehension, ``dict(zip(...))``,
    ``dict(...)`` call, ``MappingProxyType``, ...) whose string keys are two or
    more role names."""
    if not isinstance(value, Mapping):
        return False
    keys = list(value.keys())
    if not all(isinstance(key, str) for key in keys):
        return False
    if len(keys) < _MIN_GRADES_TO_COUNT:
        return False
    return set(keys) <= roles


def _is_role_keyed_pair_sequence(value: object, roles: frozenset[str]) -> bool:
    """True for a list/tuple of 2-element pairs whose first elements are two
    or more role names: the shape a dict takes before someone calls
    ``dict(...)`` on it, or chooses not to."""
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        return False
    firsts: list[str] = []
    for item in value:
        if isinstance(item, str | bytes) or not isinstance(item, Sequence):
            return False
        if len(item) != 2:
            return False
        first = item[0]
        if not isinstance(first, str):
            return False
        firsts.append(first)
    if len(firsts) < _MIN_GRADES_TO_COUNT:
        return False
    return set(firsts) <= roles


def _is_role_keyed_enum(value: object, roles: frozenset[str]) -> bool:
    """True for an ``enum.Enum``/``enum.IntEnum`` whose member names are two
    or more role names: an ordering encoded in member *values* rather than
    in a mapping at all."""
    if not (isinstance(value, type) and issubclass(value, enum.Enum)):
        return False
    names = [member.name for member in value]
    if len(names) < _MIN_GRADES_TO_COUNT:
        return False
    return set(names) <= roles


def find_role_shaped_values(
    module: ModuleType, roles: frozenset[str], *, canonical: object
) -> list[str]:
    """Names of module-level attributes whose *value* looks like a role
    priority table, judged by shape rather than by how it was written.

    ``canonical`` is excluded by identity (``is``), not by name or module
    path: any other object, even one holding identical values, is a copy.
    """
    offenders: list[str] = []
    for name, value in vars(module).items():
        if value is canonical:
            continue
        if (
            _is_role_keyed_mapping(value, roles)
            or _is_role_keyed_pair_sequence(value, roles)
            or _is_role_keyed_enum(value, roles)
        ):
            offenders.append(name)
    return offenders


def _iter_consumer_modules() -> Iterator[tuple[str, ModuleType]]:
    """Import every module under SEARCH_DIRS, top package included.

    ``pkgutil.walk_packages`` only yields submodules, not the package named
    by ``prefix`` itself, so the top-level package (e.g. plain ``core``,
    whose ``__init__.py`` could in principle hold a copy too) is imported
    separately.
    """
    for directory in SEARCH_DIRS:
        top = importlib.import_module(directory)
        if directory not in SKIPPED_MODULES:
            yield directory, top
        if not hasattr(top, "__path__"):
            continue
        for modinfo in pkgutil.walk_packages(top.__path__, prefix=directory + "."):
            if modinfo.name in SKIPPED_MODULES:
                continue
            module = importlib.import_module(modinfo.name)
            yield modinfo.name, module


def test_every_skip_states_a_reason() -> None:
    """An allow-list without reasons becomes a place to silence this guard."""
    for module_name, reason in SKIPPED_MODULES.items():
        assert len(reason.split()) >= 4, (
            f"the reason for skipping {module_name} is too short to be a reason"
        )


def test_the_guard_knows_the_role_names() -> None:
    roles = _role_names()
    assert "viewer" in roles and "super_admin" in roles, roles


def test_role_priority_has_no_lookalike_anywhere_else() -> None:
    from core.security import _ROLE_PRIORITY

    roles = _role_names()
    offenders: list[str] = []
    for module_name, module in _iter_consumer_modules():
        for attr_name in find_role_shaped_values(module, roles, canonical=_ROLE_PRIORITY):
            offenders.append(f"{module_name}.{attr_name}")

    assert not offenders, (
        "a value shaped like the role-priority table exists outside "
        "core.security, regardless of the statement that built it (dict "
        "literal, comprehension, dict(zip(...)), a list of pairs, an "
        "IntEnum, or a function assembling one at runtime): "
        + ", ".join(offenders)
        + ". Import core.security.highest_role, or _ROLE_PRIORITY itself, "
        "instead of reconstructing the order."
    )


def test_a_second_object_with_the_same_values_still_fails() -> None:
    """Identity exemption, not value equality: proven by breaking it.

    A copy that is correct on the day it is written is exactly what this
    guard exists to catch, so a dict holding the *same* values as
    ``_ROLE_PRIORITY`` but living at a different address must still be
    flagged. If this passed the guard, the exemption would be doing
    ``==`` under the hood and every future copy would slip through as long
    as nobody let the numbers drift on day one.
    """
    from core.security import _ROLE_PRIORITY

    roles = _role_names()
    identical_copy = dict(_ROLE_PRIORITY)
    assert identical_copy == _ROLE_PRIORITY
    assert identical_copy is not _ROLE_PRIORITY

    fake_module = ModuleType("role_priority_lookalike_test_module")
    fake_module._SECOND_MAP = identical_copy  # type: ignore[attr-defined]

    offenders = find_role_shaped_values(fake_module, roles, canonical=_ROLE_PRIORITY)
    assert offenders == ["_SECOND_MAP"], (
        "a dict with the same values as _ROLE_PRIORITY, but a different "
        "object, was not flagged; the identity check has degraded into an "
        "equality check"
    )


# ---------------------------------------------------------------------------
# Self-test: five ways to build a role-priority table that are not a dict
# literal, each proven to trip the guard (CLAUDE.md hardening rule #7: a
# guard that always passes tells you nothing about what it protects).
# ---------------------------------------------------------------------------

_EVASION_MODULE_SOURCES: dict[str, str] = {
    "tuple_list": textwrap.dedent(
        """
        # A list of (role, grade) pairs, which never becomes a dict at all, so an
        # ast.Dict-based guard has nothing to find here.
        _ROLE_ORDER_ITEMS = [
            ("viewer", 1),
            ("developer", 2),
            ("team_admin", 3),
            ("super_admin", 4),
        ]
        """
    ),
    "dict_comprehension": textwrap.dedent(
        """
        # A comprehension, not a dict literal: ast.walk finds a
        # ast.DictComp node here, not an ast.Dict.
        _grades = (("viewer", 1), ("developer", 2), ("team_admin", 3), ("super_admin", 4))
        _ROLE_ORDER = {name: grade for name, grade in _grades}
        """
    ),
    "dict_zip_call": textwrap.dedent(
        """
        # dict(zip(...)) is a function call; the AST guard only recognises
        # ast.Dict nodes, so a call expression is invisible to it.
        _ROLE_NAMES = ("viewer", "developer", "team_admin", "super_admin")
        _ROLE_GRADES = (1, 2, 3, 4)
        _ROLE_ORDER = dict(zip(_ROLE_NAMES, _ROLE_GRADES, strict=True))
        """
    ),
    "int_enum": textwrap.dedent(
        """
        # An ordering encoded in enum member values, not in any dict at all.
        import enum


        class RoleOrder(enum.IntEnum):
            viewer = 1
            developer = 2
            team_admin = 3
            super_admin = 4
        """
    ),
    "runtime_assembly_function": textwrap.dedent(
        """
        # Built by a function at import time. The module-level name resolves
        # to a plain dict once the module finishes executing, same as a
        # literal would, but no ast.Dict node in this source spells it out.
        def _build_role_order():
            order = {}
            order["viewer"] = 1
            order["developer"] = 2
            order["team_admin"] = 3
            order["super_admin"] = 4
            return order


        _ROLE_ORDER = _build_role_order()
        """
    ),
}


def _load_module_from_source(name: str, source: str, tmp_path: Path) -> ModuleType:
    module_file = tmp_path / f"{name}.py"
    module_file.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, module_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("shape_name", sorted(_EVASION_MODULE_SOURCES))
def test_runtime_guard_catches_every_evasion_shape(shape_name: str, tmp_path: Path) -> None:
    """Each shape below is exactly the kind of thing #387 reported: it holds
    the same judgement as ``_ROLE_PRIORITY`` and would pass the AST guard
    clean (proven separately below), yet must fail this one."""
    roles = _role_names()
    module = _load_module_from_source(
        f"role_priority_evasion_{shape_name}", _EVASION_MODULE_SOURCES[shape_name], tmp_path
    )
    offenders = find_role_shaped_values(module, roles, canonical=object())
    assert offenders, (
        f"the {shape_name} evasion shape was not caught by the runtime "
        "shape guard; issue #387 exists because exactly this kind of shape "
        "slipped past the AST-only guard"
    )


@pytest.mark.parametrize("shape_name", sorted(_EVASION_MODULE_SOURCES))
def test_ast_guard_alone_misses_every_evasion_shape(shape_name: str, tmp_path: Path) -> None:
    """The other half of hardening rule #8: confirm the AST guard is really
    blind to these, rather than assuming it from issue #387's description.
    If this test ever fails, the AST guard is no longer the reason this
    runtime guard needs to exist for that shape."""
    import ast

    from tests.unit.test_role_priority_has_one_home import _dict_literals_keyed_by_role

    roles = _role_names()
    module_file = tmp_path / f"role_priority_evasion_{shape_name}.py"
    module_file.write_text(_EVASION_MODULE_SOURCES[shape_name], encoding="utf-8")
    tree = ast.parse(module_file.read_text(encoding="utf-8"))
    assert _dict_literals_keyed_by_role(tree, roles) == [], (
        f"the {shape_name} evasion shape was caught by the AST guard after "
        "all; either it grew an actual dict literal keyed by role names, or "
        "the AST guard changed to look for more than ast.Dict"
    )


def test_runtime_guard_alone_is_not_needed_to_catch_a_plain_dict_literal() -> None:
    """The AST guard's own reason to exist, checked from this file so the
    two tests do not silently depend on each other: a plain dict literal
    keyed by role names is caught by the AST guard with no runtime import
    required at all."""
    import ast

    from tests.unit.test_role_priority_has_one_home import _dict_literals_keyed_by_role

    roles = _role_names()
    source = textwrap.dedent(
        """
        _ROLE_ORDER = {"viewer": 1, "developer": 2, "team_admin": 3, "super_admin": 4}
        """
    )
    tree = ast.parse(source)
    assert _dict_literals_keyed_by_role(tree, roles), (
        "the AST guard no longer recognises a plain dict literal keyed by "
        "role names, which is its own reason to exist"
    )
