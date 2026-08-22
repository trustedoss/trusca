# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
W2 -- shared Postgres connection-budget calculation.

One formula, several callers. Before this module existed, the same
arithmetic was hand-copied into `.env.example`'s worked examples and
`charts/trustedoss/templates/NOTES.txt`'s Helm formula, and the two drifted:
NOTES.txt's backend line was `replicaCount x (size + maxOverflow)`, missing
the uvicorn-worker multiplier `.env.example` already had, so it answered a
number 4x too small. This module is now the single oracle both derive from
(`.env.example` documents its worked examples against this formula;
`charts/trustedoss/templates/NOTES.txt` computes the same shape in Go
template arithmetic and a golden test cross-checks the two agree).

    total = backend_replicas * uvicorn_workers * (pool_size + max_overflow)
          + worker_replicas  * (sync_pool_size + sync_max_overflow)
          + beat_replicas    * (sync_pool_size + sync_max_overflow)

``backend_replicas * uvicorn_workers`` is the process count for the FastAPI
tier: each uvicorn worker is a separate OS process with its own SQLAlchemy
engine and its own pool, and each replica/pod multiplies that again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import structlog


@dataclass(frozen=True)
class ConnectionBudget:
    """One deployment's connection-pool shape.

    Field names match the env vars / Helm values they come from 1:1 so a
    caller building one from either side reads like a transcription, not a
    translation.
    """

    name: str
    backend_replicas: int
    uvicorn_workers: int
    pool_size: int
    max_overflow: int
    worker_replicas: int
    sync_pool_size: int
    sync_max_overflow: int
    max_connections: int
    # Celery beat is a singleton by design (the chart and both composes run
    # exactly one) -- not a knob, so it is not an env-var-driven field.
    beat_replicas: int = 1
    # Non-pool connections (short-lived admin/migration sessions, `psql`
    # from an operator's shell, etc). `.env.example`'s worked examples have
    # carried this fixed 5-connection allowance since the sizing formula was
    # first written; kept as a named field rather than folded into the
    # `max_connections` comparison so a caller can see it explicitly.
    admin_headroom: int = 5

    @property
    def backend_processes(self) -> int:
        return self.backend_replicas * self.uvicorn_workers

    @property
    def backend_conns(self) -> int:
        return self.backend_processes * (self.pool_size + self.max_overflow)

    @property
    def worker_conns(self) -> int:
        return self.worker_replicas * (self.sync_pool_size + self.sync_max_overflow)

    @property
    def beat_conns(self) -> int:
        return self.beat_replicas * (self.sync_pool_size + self.sync_max_overflow)

    @property
    def total_connections(self) -> int:
        """`backend + worker + beat`, WITHOUT the admin headroom.

        This is the number the W2 regression contract compares against
        `max_connections`: "process count x (pool + overflow) + worker +
        beat fits under max_connections". `total_with_headroom` below is the
        stricter number the boot-time warning and the sizing-formula worked
        examples use.
        """
        return self.backend_conns + self.worker_conns + self.beat_conns

    @property
    def total_with_headroom(self) -> int:
        return self.total_connections + self.admin_headroom

    @property
    def over_budget(self) -> bool:
        """True once the headroom-inclusive total would not fit."""
        return self.total_with_headroom > self.max_connections

    def as_log_fields(self) -> dict[str, int | str]:
        """Structured fields for the boot-time warning (no secrets to mask here)."""
        return {
            "deployment": self.name,
            "backend_replicas": self.backend_replicas,
            "uvicorn_workers": self.uvicorn_workers,
            "backend_processes": self.backend_processes,
            "backend_conns": self.backend_conns,
            "worker_replicas": self.worker_replicas,
            "worker_conns": self.worker_conns,
            "beat_conns": self.beat_conns,
            "admin_headroom": self.admin_headroom,
            "total_with_headroom": self.total_with_headroom,
            "max_connections": self.max_connections,
        }


def current_process_budget(*, max_connections: int) -> ConnectionBudget:
    """Build the budget for this deployment from its current env + Postgres.

    Reads the pool-sizing knobs the engines are actually built with
    (`core.db`) plus the three W2 fleet-shape hints an operator sets to match
    how they run the containers (`core.config.conn_budget_*`). `max_connections`
    is the caller's job to supply. The natural source is `SHOW max_connections`
    against the very database the pools connect to, which is what
    `main.py`'s boot check does; a caller with no live connection yet can pass
    the value it plans to configure.
    """
    # Local import to avoid a hard dependency at module import time / keep
    # this module importable from a context (like a Helm-parity test) that
    # only wants the dataclass and formula, not the full config module.
    from . import config

    return ConnectionBudget(
        name="runtime",
        backend_replicas=config.conn_budget_backend_replicas(),
        uvicorn_workers=config.conn_budget_uvicorn_workers(),
        pool_size=config.db_pool_size(),
        max_overflow=config.db_max_overflow(),
        worker_replicas=config.conn_budget_worker_replicas(),
        sync_pool_size=config.db_sync_pool_size(),
        sync_max_overflow=config.db_sync_max_overflow(),
        max_connections=max_connections,
    )


def log_if_over_budget(log: structlog.stdlib.BoundLogger, budget: ConnectionBudget) -> bool:
    """Emit a WARNING when `budget` would exceed its `max_connections`.

    Returns whether it warned, so a caller (or a test) can assert on the
    outcome without re-deriving `over_budget`. Silent when the budget fits,
    matching `core.config`'s existing "warn only on correction" convention
    for clamped/invalid env values.
    """
    if not budget.over_budget:
        return False
    log.warning("connection_budget.over_max_connections", **budget.as_log_fields())
    return True
