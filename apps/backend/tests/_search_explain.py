# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Shared ``EXPLAIN`` capture helpers for the M3 search query-plan regression
tests (concurrency-scaling plan, unit 13).

Not a ``test_*`` module itself: imported by
``tests/integration/test_search_query_plan_contracts.py`` (miniature, runs on
every PR) and ``tests/integration/test_search_explain_load_baseline.py``
(heavy, requires ``scripts/seed_load_test.py`` to have already run).

Why capture-then-EXPLAIN instead of hand-building the query
-------------------------------------------------------------

The services under test (``services.search_results_service._components`` and
friends) build a ``Select`` and immediately ``session.execute()`` it: they do
not expose the compiled statement. Re-deriving an equivalent query by hand in
the test file would drift from the real one the first time a WHERE clause or
join changes here but not there, silently invalidating the regression. So
instead we hook ``before_cursor_execute`` (the same technique
``test_action_queue_query_count.py`` and ``test_request_query_budget.py``
already use for query-count assertions), let the real service call run, and
re-issue the EXACT SQL text + bound parameters it sent wrapped in
``EXPLAIN``. The asyncpg dialect compiles to ``$1, $2, ...`` positional
placeholders, so the captured statement text is valid driver-native SQL that
``AsyncConnection.exec_driver_sql`` can replay unmodified alongside the same
parameter tuple.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import event

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


async def explain_nth_statement(
    session: AsyncSession,
    call: Callable[[], Awaitable[T]],
    *,
    index: int,
    analyze: bool = False,
) -> tuple[T, dict[str, Any], str]:
    """Run ``call()`` and ``EXPLAIN`` the ``index``-th statement it issued.

    ``index`` counts from 0 in issue order (``services.search_results_service``
    always issues COUNT, then the page SELECT, then the facet query, for the
    ``components`` kind: see that module's ``_components`` for the order).

    ``analyze=True`` adds ``ANALYZE, BUFFERS`` (actually executes the query a
    second time to get real row/buffer counts): use this only against a
    dataset large enough that the extra execution is cheap relative to the
    thing being measured. The miniature PR-gate tests use the default
    (``analyze=False``, plan-only) precisely so they stay fast and so a
    forced ``enable_seqscan = off`` (see below) never has to actually walk
    real rows.

    Returns ``(call_result, plan_dict, captured_sql)``. ``plan_dict`` is
    Postgres's ``FORMAT JSON`` output for this one statement (the top-level
    array's single element, already unwrapped here) so it still has its own
    top-level ``"Plan"`` key alongside siblings like ``"Planning Time"`` and
    ``"Execution Time"``; callers read ``plan_dict["Plan"]`` to get the root
    plan node, the same shape ``EXPLAIN (FORMAT JSON)`` always returns.
    """
    captured: list[tuple[str, Any]] = []
    engine = session.get_bind()

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]  # noqa: ANN001, ARG001
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _record)
    try:
        result = await call()
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(captured) > index, (
        f"expected at least {index + 1} statement(s) from call(), "
        f"captured {len(captured)}: {[s for s, _ in captured]}"
    )
    stmt, params = captured[index]
    mode = "ANALYZE, BUFFERS, FORMAT JSON" if analyze else "FORMAT JSON"
    conn = await session.connection()
    raw = await conn.exec_driver_sql(f"EXPLAIN ({mode}) {stmt}", params)
    row = raw.first()
    assert row is not None, "EXPLAIN produced no output row"
    payload = row[0]
    plan_list = json.loads(payload) if isinstance(payload, str) else payload
    return result, plan_list[0], stmt


def index_names_in_plan(node: dict[str, Any]) -> set[str]:
    """Recursively collect every ``Index Name`` anywhere in the plan tree."""
    found: set[str] = set()
    idx = node.get("Index Name")
    if idx:
        found.add(idx)
    for child in node.get("Plans", []):
        found |= index_names_in_plan(child)
    return found


def node_types_in_plan(node: dict[str, Any]) -> set[str]:
    """Recursively collect every ``Node Type`` anywhere in the plan tree."""
    found = {node.get("Node Type", "")}
    for child in node.get("Plans", []):
        found |= node_types_in_plan(child)
    return found


def sort_methods_in_plan(node: dict[str, Any]) -> list[str]:
    """Recursively collect every ``Sort Method`` string (ANALYZE-only field)."""
    found: list[str] = []
    method = node.get("Sort Method")
    if method:
        found.append(str(method))
    for child in node.get("Plans", []):
        found.extend(sort_methods_in_plan(child))
    return found


def total_buffers(node: dict[str, Any]) -> int:
    """Recursively sum shared-hit + shared-read blocks (ANALYZE BUFFERS-only).

    A per-node proxy for "how much work Postgres did to answer this query":
    used to compare the current all-scan-history query against a
    latest-scan-only variant without needing to locate one specific node by
    name (the planner is free to fuse/reorder joins between the two shapes).
    """
    total = int(node.get("Shared Hit Blocks", 0)) + int(node.get("Shared Read Blocks", 0))
    for child in node.get("Plans", []):
        total += total_buffers(child)
    return total


def total_actual_rows(node: dict[str, Any], *, relation: str) -> int:
    """Recursively sum ``Actual Rows`` for every node scanning ``relation``.

    ``Actual Rows`` is per-loop; multiply by ``Actual Loops`` (defaults to 1)
    to get the true total the way ``EXPLAIN ANALYZE``'s own text output does.
    """
    total = 0
    if node.get("Relation Name") == relation:
        total += int(node.get("Actual Rows", 0)) * int(node.get("Actual Loops", 1))
    for child in node.get("Plans", []):
        total += total_actual_rows(child, relation=relation)
    return total
