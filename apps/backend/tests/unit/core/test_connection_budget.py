# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
W2: connection-budget formula and its regression contract.

concurrency-scaling-plan-2026-08-22.md §4, the W2 row: "whatever the
deployment default, `process count x (pool + overflow) + worker + beat`
fits inside that deployment's `max_connections`." This file is that
contract, parametrized over every deployment shape this repo ships
(prod/dev/demo compose, Helm), plus unit coverage for the
`ConnectionBudget` dataclass and the boot-time warning helper.

The Helm shape here is asserted to match `charts/trustedoss/templates/NOTES.txt`
by test_helm_notes_connection_budget.py (tests/integration/), which renders
the actual chart and cross-checks the two agree; this file only encodes the
formula and the "does it fit" contract, not the Helm template's own render.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from core.connection_budget import (
    ConnectionBudget,
    current_process_budget,
    log_if_over_budget,
)

# ---------------------------------------------------------------------------
# Known deployment shapes. The source values are the compose files / Helm
# values.yaml themselves, re-derived here rather than imported (there is no
# shared Python object those YAML files could export). A change to any of
# these files' pool sizing, replica counts, or max_connections should be
# reflected here in the same PR, or this contract stops meaning anything.
# ---------------------------------------------------------------------------

PROD_COMPOSE = ConnectionBudget(
    name="docker-compose.yml (prod)",
    backend_replicas=1,  # no `deploy.replicas` on the backend service
    uvicorn_workers=4,  # Dockerfile.prod CMD --workers 4, no override
    pool_size=5,
    max_overflow=3,
    worker_replicas=1,  # WORKER_REPLICAS default
    sync_pool_size=3,
    sync_max_overflow=3,
    max_connections=100,  # no `command:` tuning on the postgres service
)

DEV_COMPOSE = ConnectionBudget(
    name="docker-compose.dev.yml",
    backend_replicas=1,
    uvicorn_workers=1,  # no --workers flag -> uvicorn's own default
    pool_size=5,
    max_overflow=3,
    worker_replicas=1,
    sync_pool_size=3,
    sync_max_overflow=3,
    max_connections=100,
)

DEMO_COMPOSE = ConnectionBudget(
    name="docker-compose.demo.yml",
    backend_replicas=1,
    uvicorn_workers=2,  # explicit `--workers 2` override
    pool_size=5,  # explicit DB_POOL_SIZE override (matches the new base default)
    max_overflow=5,  # explicit DB_MAX_OVERFLOW override
    worker_replicas=1,  # `deploy.replicas: 1`
    sync_pool_size=3,  # not overridden -> base default
    sync_max_overflow=3,  # not overridden -> base default
    max_connections=60,  # `-c max_connections=60`
)

HELM_DEFAULT = ConnectionBudget(
    name="charts/trustedoss values.yaml",
    backend_replicas=2,  # backend.replicaCount
    uvicorn_workers=4,  # backend.uvicornWorkers (documents Dockerfile.prod)
    pool_size=5,  # env.dbPool.size
    max_overflow=3,  # env.dbPool.maxOverflow
    # S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4): worker.replicaCount
    # no longer exists post-queue-split -- worker.scan.replicaCount(2) +
    # worker.default.replicaCount(1), since ConnectionBudget treats "worker
    # replicas" as one uniform pool regardless of which queue kind it drains
    # (see configmap-env.yaml's CONN_BUDGET_WORKER_REPLICAS comment).
    worker_replicas=3,
    sync_pool_size=3,  # env.dbPool.syncSize
    sync_max_overflow=3,  # env.dbPool.syncMaxOverflow
    max_connections=100,  # statefulset-postgres.yaml applies no tuning
)

KNOWN_DEPLOYMENTS = [PROD_COMPOSE, DEV_COMPOSE, DEMO_COMPOSE, HELM_DEFAULT]


@pytest.mark.parametrize("budget", KNOWN_DEPLOYMENTS, ids=lambda b: b.name)
def test_every_shipped_deployment_default_fits_its_max_connections(
    budget: ConnectionBudget,
) -> None:
    """W2 regression contract (plan §4): the whole point of this unit.

    "process count x (pool + overflow) + worker + beat" must stay under
    max_connections for EVERY deployment default this repo ships, including
    the ~5-connection admin/headroom margin the sizing-formula docs promise.
    """
    assert not budget.over_budget, (
        f"{budget.name}: total_with_headroom={budget.total_with_headroom} "
        f"exceeds max_connections={budget.max_connections}"
    )


@pytest.mark.parametrize("budget", KNOWN_DEPLOYMENTS, ids=lambda b: b.name)
def test_backend_tier_alone_would_have_been_over_budget_at_the_old_defaults(
    budget: ConnectionBudget,
) -> None:
    """Proves W2 fixed something real, not just moved numbers around.

    Re-running the SAME shapes at the pre-W2 defaults (20 + 10 pool / sync
    5 + 5) reproduces the plan's §1.6 finding: prod compose and Helm were
    over budget on the backend tier alone, before the worker/beat pools are
    even added.
    """
    import dataclasses

    old_defaults = dataclasses.replace(
        budget,
        pool_size=20,
        max_overflow=10,
        sync_pool_size=5,
        sync_max_overflow=5,
    )
    if budget.name in {"docker-compose.yml (prod)", "charts/trustedoss values.yaml"}:
        assert old_defaults.over_budget, (
            f"{budget.name}: expected the pre-W2 defaults to be over budget "
            "(that was the bug W2 fixes) but they were not; the fixture no "
            "longer reproduces concurrency-scaling-plan-2026-08-22.md §1.6"
        )


# ---------------------------------------------------------------------------
# ConnectionBudget -- dataclass arithmetic
# ---------------------------------------------------------------------------


def test_backend_processes_multiplies_replicas_by_uvicorn_workers() -> None:
    """The bug W2 fixes: NOTES.txt dropped exactly this multiplication."""
    budget = ConnectionBudget(
        name="t",
        backend_replicas=2,
        uvicorn_workers=4,
        pool_size=5,
        max_overflow=3,
        worker_replicas=1,
        sync_pool_size=3,
        sync_max_overflow=3,
        max_connections=100,
    )
    assert budget.backend_processes == 8
    assert budget.backend_conns == 8 * 8  # 8 processes x (5 + 3)
    assert budget.worker_conns == 6  # 1 x (3 + 3)
    assert budget.beat_conns == 6  # 1 (default) x (3 + 3)
    assert budget.total_connections == 64 + 6 + 6
    assert budget.total_with_headroom == budget.total_connections + 5  # default headroom


def test_over_budget_is_false_exactly_at_the_boundary() -> None:
    """total_with_headroom == max_connections fits; one more does not."""
    budget = ConnectionBudget(
        name="t",
        backend_replicas=1,
        uvicorn_workers=1,
        pool_size=5,
        max_overflow=0,
        worker_replicas=0,
        sync_pool_size=1,
        sync_max_overflow=0,
        beat_replicas=0,
        admin_headroom=0,
        max_connections=5,
    )
    assert budget.total_with_headroom == 5
    assert budget.over_budget is False

    over = ConnectionBudget(**{**budget.__dict__, "max_connections": 4})
    assert over.over_budget is True


def test_beat_replicas_default_to_one_singleton() -> None:
    budget = ConnectionBudget(
        name="t",
        backend_replicas=1,
        uvicorn_workers=1,
        pool_size=1,
        max_overflow=0,
        worker_replicas=0,
        sync_pool_size=2,
        sync_max_overflow=0,
        max_connections=100,
    )
    assert budget.beat_replicas == 1
    assert budget.beat_conns == 2


def test_as_log_fields_carries_the_numbers_a_warning_needs() -> None:
    budget = HELM_DEFAULT
    fields = budget.as_log_fields()
    assert fields["deployment"] == HELM_DEFAULT.name
    assert fields["backend_processes"] == 8
    assert fields["total_with_headroom"] == budget.total_with_headroom
    assert fields["max_connections"] == 100


# ---------------------------------------------------------------------------
# current_process_budget -- reads the live config accessors
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env() -> Iterator[None]:
    keys = [
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_SYNC_POOL_SIZE",
        "DB_SYNC_MAX_OVERFLOW",
        "UVICORN_WORKERS",
        "CONN_BUDGET_BACKEND_REPLICAS",
        "CONN_BUDGET_WORKER_REPLICAS",
    ]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_current_process_budget_matches_the_prod_compose_defaults() -> None:
    """No env overrides -> the same numbers PROD_COMPOSE encodes by hand."""
    budget = current_process_budget(max_connections=100)
    assert budget.backend_replicas == PROD_COMPOSE.backend_replicas
    assert budget.uvicorn_workers == PROD_COMPOSE.uvicorn_workers
    assert budget.pool_size == PROD_COMPOSE.pool_size
    assert budget.max_overflow == PROD_COMPOSE.max_overflow
    assert budget.worker_replicas == PROD_COMPOSE.worker_replicas
    assert budget.sync_pool_size == PROD_COMPOSE.sync_pool_size
    assert budget.sync_max_overflow == PROD_COMPOSE.sync_max_overflow
    assert budget.total_connections == PROD_COMPOSE.total_connections
    assert not budget.over_budget


def test_current_process_budget_reflects_env_overrides() -> None:
    os.environ["UVICORN_WORKERS"] = "8"
    os.environ["CONN_BUDGET_BACKEND_REPLICAS"] = "4"
    budget = current_process_budget(max_connections=100)
    assert budget.backend_processes == 32
    assert budget.over_budget is True


# ---------------------------------------------------------------------------
# log_if_over_budget -- boot-time warning
# ---------------------------------------------------------------------------


def test_log_if_over_budget_stays_quiet_when_it_fits() -> None:
    from unittest.mock import MagicMock

    fake_logger = MagicMock()
    warned = log_if_over_budget(fake_logger, PROD_COMPOSE)
    assert warned is False
    fake_logger.warning.assert_not_called()


def test_log_if_over_budget_warns_with_the_breakdown_when_it_does_not() -> None:
    import dataclasses
    from unittest.mock import MagicMock

    over_budget = dataclasses.replace(HELM_DEFAULT, max_connections=50)
    fake_logger = MagicMock()
    warned = log_if_over_budget(fake_logger, over_budget)
    assert warned is True
    fake_logger.warning.assert_called_once()
    args, kwargs = fake_logger.warning.call_args
    assert args[0] == "connection_budget.over_max_connections"
    assert kwargs["max_connections"] == 50
    assert kwargs["total_with_headroom"] == over_budget.total_with_headroom
    assert kwargs["deployment"] == HELM_DEFAULT.name
