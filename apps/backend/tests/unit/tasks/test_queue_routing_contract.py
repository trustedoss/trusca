# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4, S3 row): vocabulary
consistency between ``tasks/celery_app.py``'s ``task_routes`` and
``tests/contracts/queue-names.json``.

Repository hardening rule 2: when the same vocabulary exists in two or more
places, an equality test is mandatory. Here it exists in three: the JSON
contract (the single source both devops's chart/compose ``-Q`` arguments and
this backend module are meant to track, per the file's own ``$comment``),
this module's literal ``_SCAN_QUEUE`` / ``_DEFAULT_QUEUE`` / ``_SCAN_TASK_NAMES``
constants, and the actual scan task modules' own ``name=`` kwargs.
``celery_app.py`` cannot read the JSON file at runtime (it lives outside
``apps/backend``, the Docker build context for the backend/worker images;
see the constants' own docstring), so this test is the only thing that would
catch the two drifting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPO_ROOT / "tests" / "contracts" / "queue-names.json"


def _load_contract() -> dict[str, Any]:
    contract: dict[str, Any] = json.loads(CONTRACT_PATH.read_text())
    return contract


def _contract_scan_queue(contract: dict[str, Any]) -> str:
    return str(contract["scan_queue"])


def _contract_default_queue(contract: dict[str, Any]) -> str:
    return str(contract["default_queue"])


def _contract_scan_task_names(contract: dict[str, Any]) -> set[str]:
    return {str(name) for name in contract["scan_task_names"]}


def test_contract_file_exists_and_parses() -> None:
    assert CONTRACT_PATH.is_file(), f"expected queue-names.json at {CONTRACT_PATH}"
    contract = _load_contract()
    assert _contract_scan_queue(contract)
    assert _contract_default_queue(contract)
    assert _contract_scan_task_names(contract)


def test_celery_app_queue_names_match_the_contract() -> None:
    from tasks.celery_app import _DEFAULT_QUEUE, _SCAN_QUEUE

    contract = _load_contract()
    assert _SCAN_QUEUE == _contract_scan_queue(contract)
    assert _DEFAULT_QUEUE == _contract_default_queue(contract)


def test_celery_app_scan_task_names_match_the_contract() -> None:
    from tasks.celery_app import _SCAN_TASK_NAMES

    contract = _load_contract()
    assert set(_SCAN_TASK_NAMES) == _contract_scan_task_names(contract)


def test_task_default_queue_equals_the_pre_split_single_queue_name() -> None:
    """The transition's whole premise (contract file's own ``$comment``): the
    post-split default queue name is the SAME string the single-queue
    deployment already used as ``task_default_queue``, so a worker that still
    subscribes to it drains pre-split messages with no rename step."""
    from tasks.celery_app import create_celery_app

    contract = _load_contract()
    app = create_celery_app()
    assert app.conf.task_default_queue == _contract_default_queue(contract)


def test_task_routes_sends_every_scan_task_name_to_the_scan_queue() -> None:
    from tasks.celery_app import create_celery_app

    contract = _load_contract()
    scan_queue = _contract_scan_queue(contract)
    app = create_celery_app()
    routes = app.conf.task_routes
    assert routes is not None
    for name in _contract_scan_task_names(contract):
        assert routes[name] == {"queue": scan_queue}, (
            f"{name!r} is not routed to {scan_queue!r}: task_routes has " f"{routes.get(name)!r}"
        )


def test_task_routes_does_not_route_a_non_scan_task() -> None:
    """A representative non-scan task name must fall through to
    ``task_default_queue`` rather than being explicitly routed. Pins that
    ``task_routes`` only ever grows to cover the scan_task_names set, not
    silently absorb an unrelated task."""
    from tasks.celery_app import create_celery_app

    contract = _load_contract()
    app = create_celery_app()
    routes = app.conf.task_routes
    assert routes is not None
    assert "trustedoss.notify" not in _contract_scan_task_names(contract)
    assert "trustedoss.notify" not in routes


def test_scan_task_names_match_the_real_task_modules_name_kwarg() -> None:
    """Cross-checks the contract's ``scan_task_names`` against the actual
    ``@celery_app.task(name=...)`` strings the four scan-pipeline task
    modules register. The contract's list is meant to enumerate exactly
    these four, not a hand-typed guess at their names."""
    from tasks.ingest_sbom import ingest_sbom_task
    from tasks.scan_container import scan_container_task
    from tasks.scan_reachability import scan_reachability_task
    from tasks.scan_source import scan_source_task

    contract = _load_contract()
    actual_names = {
        scan_source_task.name,
        scan_container_task.name,
        ingest_sbom_task.name,
        scan_reachability_task.name,
    }
    assert actual_names == _contract_scan_task_names(contract)
