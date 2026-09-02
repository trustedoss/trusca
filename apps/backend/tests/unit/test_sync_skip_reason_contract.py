# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The sync-state skip-reason vocabulary has exactly one definition.

Before ``models.sync_state`` existed the same closed set was restated in five
places: three model docstrings and a bare literal at every assignment inside
the three refresh tasks. They drifted, which is how ``kev_sync_state`` came to
omit ``refresh_disabled`` and how three eol-only reasons came to appear in no
model docstring at all.

Prose cannot be diffed against code, so these tests work on the parsed source
instead. The first one is the load-bearing check: it fails if any refresh task
assigns a bare string to ``skipped_reason`` again, which is the exact move that
created the drift.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from models.sync_state import (
    SYNC_SKIPPED_REASON_UNEXPECTED_PREFIX,
    SYNC_SKIPPED_REASON_VALUES,
    is_valid_skipped_reason,
    unexpected_reason,
)

#: The tasks whose skip reasons land in a ``*_sync_state`` row. Other periodic
#: tasks put a ``skipped_reason`` in their summary dict too, but those never
#: reach these tables and use a different vocabulary (see the module docstring
#: of ``models.sync_state``).
_SYNC_TASKS = (
    "eol_catalog_refresh.py",
    "kev_catalog_refresh.py",
    "malicious_catalog_refresh.py",
)

_MODELS = (
    "eol_sync_state.py",
    "kev_sync_state.py",
    "malicious_sync_state.py",
)

_BACKEND = Path(__file__).resolve().parents[2]


def _assignments_to_skipped_reason(source: str) -> list[ast.expr]:
    """Every value assigned to a ``...["skipped_reason"]`` subscript."""
    tree = ast.parse(source)
    values: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "skipped_reason"
            ):
                values.append(node.value)
    return values


@pytest.mark.parametrize("task_file", _SYNC_TASKS)
def test_refresh_tasks_assign_no_bare_skip_reason_literal(task_file: str) -> None:
    """A refresh task never writes the reason as an inline string.

    This is the regression guard. A bare literal is how a fourth copy of the
    vocabulary gets born, and it is invisible to every other test because the
    value still round-trips through the database perfectly well.
    """
    source = (_BACKEND / "tasks" / task_file).read_text(encoding="utf-8")
    assigned = _assignments_to_skipped_reason(source)
    assert assigned, f"{task_file} assigns no skipped_reason at all"

    literals = [
        node.value
        for node in assigned
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert literals == [], (
        f"{task_file} assigns skipped_reason as a bare string literal "
        f"{literals}. Import the member from models.sync_state instead so "
        f"there stays exactly one definition."
    )


@pytest.mark.parametrize("task_file", _SYNC_TASKS)
def test_refresh_tasks_only_use_known_vocabulary(task_file: str) -> None:
    """Names assigned to ``skipped_reason`` resolve to this module's exports.

    Catches an import from somewhere else, and a local variable that happens to
    be named like a member.
    """
    import models.sync_state as vocab

    exported = {
        name
        for name in dir(vocab)
        if name.startswith("SYNC_SKIPPED_") and not name.endswith("_VALUES")
    }
    source = (_BACKEND / "tasks" / task_file).read_text(encoding="utf-8")

    def check(node: ast.expr) -> None:
        if isinstance(node, ast.Name):
            assert node.id in exported, (
                f"{task_file} assigns skipped_reason from {node.id!r}, which "
                f"is not exported by models.sync_state"
            )
        elif isinstance(node, ast.Call):
            func = node.func
            assert isinstance(func, ast.Name) and func.id == "unexpected_reason", (
                f"{task_file} builds skipped_reason with an unexpected call; "
                f"only unexpected_reason() is allowed"
            )
        elif isinstance(node, ast.Subscript):
            # Carrying an already-validated reason across dicts, e.g. the eol
            # task copying summary's reason onto the row values. No new
            # vocabulary enters here.
            assert isinstance(
                node.slice, ast.Constant
            ), f"{task_file} copies skipped_reason from a computed subscript"
        elif isinstance(node, ast.BoolOp):
            # ``summary[...] or DEFAULT``: every branch must itself be valid.
            for value in node.values:
                check(value)
        else:  # pragma: no cover - defended by the previous test
            raise AssertionError(
                f"{task_file} assigns skipped_reason from an unsupported "
                f"expression: {ast.dump(node)[:120]}"
            )

    for node in _assignments_to_skipped_reason(source):
        check(node)


@pytest.mark.parametrize("model_file", _MODELS)
def test_model_docstrings_do_not_restate_the_vocabulary(model_file: str) -> None:
    """No sync-state model lists the members in prose.

    A restated list is what drifted. The docstrings now point at
    ``models.sync_state`` instead, and this test keeps them pointing.
    """
    source = (_BACKEND / "models" / model_file).read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstring = ast.get_docstring(tree) or ""

    restated = [member for member in SYNC_SKIPPED_REASON_VALUES if f"``{member}``" in docstring]
    assert restated == [], (
        f"{model_file} restates {restated} in its module docstring. Point at "
        f"models.sync_state instead: a prose copy cannot be diffed against the "
        f"code and this is exactly how kev_sync_state lost refresh_disabled."
    )


def test_vocabulary_has_no_duplicates() -> None:
    assert len(set(SYNC_SKIPPED_REASON_VALUES)) == len(SYNC_SKIPPED_REASON_VALUES)


def test_unexpected_reason_is_built_from_the_exception_class() -> None:
    assert unexpected_reason(TimeoutError("boom")) == "unexpected:TimeoutError"


def test_unexpected_reason_drops_the_exception_message() -> None:
    """The column is 64 chars and upstream messages can carry a credential."""
    secret = "https://user:hunter2@feed.example/x"
    assert secret not in unexpected_reason(RuntimeError(secret))


def test_unexpected_reason_fits_the_column() -> None:
    """Longest realistic form still fits ``String(64)``."""

    class AnExceptionWithAnUnusuallyLongClassNameForTesting(Exception):
        pass

    assert len(unexpected_reason(AnExceptionWithAnUnusuallyLongClassNameForTesting())) <= 64


@pytest.mark.parametrize("member", SYNC_SKIPPED_REASON_VALUES)
def test_every_member_validates(member: str) -> None:
    assert is_valid_skipped_reason(member)


def test_unexpected_form_validates() -> None:
    assert is_valid_skipped_reason(unexpected_reason(ValueError()))


def test_bare_prefix_does_not_validate() -> None:
    """``unexpected:`` with nothing after it carries no diagnosis."""
    assert not is_valid_skipped_reason(SYNC_SKIPPED_REASON_UNEXPECTED_PREFIX)


def test_unknown_reason_does_not_validate() -> None:
    assert not is_valid_skipped_reason("made_up_reason")


def test_other_task_families_are_not_members() -> None:
    """Reasons from non-sync tasks stay out of this vocabulary.

    ``vulnerability_rematch`` and friends also emit a ``skipped_reason`` into
    their summary dict, but those values never reach a sync-state row. If one
    ever appears here, the module docstring's scope paragraph is stale.
    """
    for foreign in ("trivy_timeout", "metrics_disabled", "invalid_scan_id"):
        assert foreign not in SYNC_SKIPPED_REASON_VALUES
