# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A column a user can edit must survive the row being replaced (ER68).

``vulnerability_findings`` rows are per-scan. A rescan inserts new ones, and
the rematch beat DELETEs and re-inserts them on a six-hour schedule that nobody
triggers. Anything a person wrote on the old row is gone unless
``persist_trivy_findings`` copies it forward.

This has now happened twice, both times the same way. Triage went first: forty
findings excluded as "not affected" came back open and counted against the
build gate again, with the justification and the analyst's name gone. Then
ER28a added assignee, deadline and ticket, and those lasted at most six hours
while the user guide promised without qualification that a finding carries who
is fixing it and by when.

Both times the columns were added correctly everywhere they were written and
read. What was not asked was who DELETEs the row.

Why this compares two sources rather than reading one list
----------------------------------------------------------
A hand-written list of "columns a user can edit" would be a third place the
same vocabulary lives, and lists like that drift in exactly the way this guard
exists to catch. The PATCH request models already know the answer: a column the
API cannot be asked to change is not a column a user can edit. So the editable
set is derived from the routes, and compared against the carry-forward set.
Open a new column for editing and forget the carry-forward, and this fails
before the data does.

The route walk is deliberately over ``router.routes`` rather than a named list
of models: a new PATCH route with a new body model is covered the moment it is
registered.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from api.v1.vulnerabilities import router
from models import VulnerabilityFinding
from services.vulnerability_matching import (
    _ASSIGNMENT_FIELDS,
    _TRIAGE_FIELDS,
    CARRIED_FIELDS,
)
from services.vulnerability_service import ASSIGNMENT_FIELDS

#: Request fields that are not themselves column names, each naming the column
#: it writes, or ``None`` when it writes no column at all. A field whose own
#: name is a column must NOT appear here: mapping one to ``None`` would be the
#: one edit that silences this guard, so ``test_the_mapping_cannot_hide_a_column``
#: refuses it.
FIELD_TO_COLUMN: dict[str, str | None] = {
    # Optimistic-concurrency token. Compared against ``updated_at``, never
    # stored.
    "if_match": None,
    # Addressing, not content: which rows the bulk call is about.
    "finding_ids": None,
    # The bulk endpoint's spelling of ``status``.
    "target_status": "status",
    # Free-form note recorded as ``analysis_justification``.
    "justification": "analysis_justification",
}

#: Editable columns deliberately NOT carried forward, each with the reason.
#: Empty today. An entry here makes an omission a decision somebody wrote down
#: rather than something nobody noticed.
NOT_CARRIED: dict[str, str] = {}


def _column_names() -> set[str]:
    return {attr.key for attr in VulnerabilityFinding.__mapper__.column_attrs}


def _write_route_body_models() -> list[tuple[str, type[BaseModel]]]:
    """(path, body model) for every write route this router registers."""
    found: list[tuple[str, type[BaseModel]]] = []
    for route in router.routes:
        # ``routes`` is typed as Starlette's BaseRoute; only FastAPI's APIRoute
        # carries a dependant, and only those are the ones with a body.
        if not isinstance(route, APIRoute):
            continue
        methods: set[str] = set(route.methods or set())
        if not methods & {"PATCH", "POST", "PUT"}:
            continue
        for param in route.dependant.body_params:
            annotation: Any = param.field_info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                found.append((route.path, annotation))
    return found


def test_the_route_walk_finds_the_write_endpoints() -> None:
    """The discovery itself has to be able to fail.

    A walk that silently returns nothing would make every assertion below pass
    while checking no route at all, which is the failure shape this whole file
    is about. So the two endpoints that exist today are named.
    """
    paths = {path for path, _ in _write_route_body_models()}
    assert "/v1/vulnerability_findings/{finding_id}/assignment" in paths
    assert "/v1/vulnerability_findings/{finding_id}/status" in paths
    assert len(_write_route_body_models()) >= 3


def test_every_editable_column_is_carried_forward() -> None:
    columns = _column_names()
    carried = set(CARRIED_FIELDS)

    for path, model in _write_route_body_models():
        for field in model.model_fields:
            if field in columns:
                target: str | None = field
            elif field in FIELD_TO_COLUMN:
                target = FIELD_TO_COLUMN[field]
            else:
                pytest.fail(
                    f"{model.__name__}.{field} (on {path}) is neither a column of "
                    "vulnerability_findings nor declared in FIELD_TO_COLUMN. Say "
                    "which column it writes, or None if it writes none, so this "
                    "guard can tell whether a rescan would drop it."
                )
            if target is None:
                continue
            assert target in carried or target in NOT_CARRIED, (
                f"{model.__name__}.{field} writes vulnerability_findings.{target}, "
                "which persist_trivy_findings does not carry forward. A rescan "
                "replaces the row and the rematch beat replaces it every six "
                "hours, so whatever a person puts there is lost without anybody "
                "doing anything. Add it to _TRIAGE_FIELDS or _ASSIGNMENT_FIELDS "
                "in services.vulnerability_matching, or record why not in "
                "NOT_CARRIED."
            )


def test_the_mapping_cannot_hide_a_column() -> None:
    """FIELD_TO_COLUMN may only name fields that are not columns.

    Without this, the cheapest way to make the guard above go quiet is to map
    the offending column to ``None``, which reads as a declaration and is a
    deletion of the check.
    """
    columns = _column_names()
    overlap = set(FIELD_TO_COLUMN) & columns
    assert not overlap, (
        f"{sorted(overlap)} are columns of vulnerability_findings and must not "
        "be redirected through FIELD_TO_COLUMN"
    )
    for field, target in FIELD_TO_COLUMN.items():
        assert target is None or target in columns, (
            f"FIELD_TO_COLUMN[{field!r}] names {target!r}, which is not a column"
        )


def test_the_two_carried_groups_stay_disjoint_and_complete() -> None:
    """CARRIED_FIELDS is exactly its two halves, and they do not overlap.

    A field in both groups would be copied twice with the second copy winning,
    which is harmless until the two groups disagree about what it means.
    """
    assert set(_TRIAGE_FIELDS) & set(_ASSIGNMENT_FIELDS) == set()
    assert set(CARRIED_FIELDS) == set(_TRIAGE_FIELDS) | set(_ASSIGNMENT_FIELDS)
    assert len(CARRIED_FIELDS) == len(set(CARRIED_FIELDS))


def test_the_assignment_vocabulary_has_one_spelling() -> None:
    """The carry-forward and the PATCH service name the same four columns.

    ``services.vulnerability_service.ASSIGNMENT_FIELDS`` is what the endpoint
    accepts; ``_ASSIGNMENT_FIELDS`` is what a re-persist restores. If they part
    company, a field becomes editable and unrecoverable, which is the defect
    this file exists for.
    """
    assert set(_ASSIGNMENT_FIELDS) == set(ASSIGNMENT_FIELDS)


def test_every_carried_field_is_a_real_column() -> None:
    """A typo in either group would be copied as an attribute that is not
    persisted, and nothing else would notice."""
    columns = _column_names()
    missing = [f for f in CARRIED_FIELDS if f not in columns]
    assert not missing, f"not columns of vulnerability_findings: {missing}"
