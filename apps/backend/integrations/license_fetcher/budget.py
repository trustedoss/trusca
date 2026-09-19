# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Per-scan time budget and circuit breaker for licence enrichment.

Registry lookups run once per unlicensed component and each costs a network
round trip, so a scan's enrichment time grows with the number of components
rather than with anything the operator configured. Nothing bounded it: a
document with ~10,000 unlicensed gem/pypi components spends its whole
scan time limit here (measured at ~0.34 s per lookup).

One :class:`EnrichmentBudget` covers one persist call and does two things.

* **Time budget.** Once the seconds spent on cache-miss lookups reach the
  budget, the remaining lookups are not made. The scan does not fail; the
  components stay licence-unknown and the budget records how many were left.
* **Circuit breaker.** After ``breaker_threshold`` consecutive lookups whose
  request never got an answer (connection error, timeout, 429 or 5xx through
  every retry), the remaining lookups are skipped. A registry that answers
  "not found" (404) is a working registry and resets the count.

A skipped lookup is *not looked up*, which is different from *looked up and
found nothing*. The dispatcher therefore writes no cache row for it, and a
lookup that hit a transport failure is not cached as a negative either: both
would otherwise read as a confirmed miss for the next 24 hours.

The budget is checked between lookups, not inside one, so a scan can overrun
it by the length of a single lookup (at most the retries times the request
timeout).

The budget reaches :func:`base.request_with_retry` through a context variable
so the seven adapters keep their signatures.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Final

REASON_BUDGET: Final = "budget_exhausted"
REASON_BREAKER: Final = "breaker_open"


@dataclass
class EnrichmentBudget:
    """Mutable, single-scan accounting. Not shared between scans or threads."""

    budget_seconds: float
    breaker_threshold: int
    spent_seconds: float = 0.0
    consecutive_failures: int = 0
    transport_failures: int = 0
    lookups: int = 0
    skipped_budget: int = 0
    skipped_breaker: int = 0
    _tripped: bool = field(default=False, repr=False)

    def refusal(self, in_flight_seconds: float = 0.0) -> str | None:
        """Why the next lookup must not run, or ``None`` when it may.

        ``in_flight_seconds`` is the time the calling lookup has already used
        and has not yet reported through :meth:`add_elapsed`. A lookup that
        makes several requests (a Maven parent chain) passes it before each
        further request, so the budget can end it part-way instead of only
        between lookups.
        """
        if self._tripped:
            return REASON_BREAKER
        if self.spent_seconds + max(in_flight_seconds, 0.0) >= self.budget_seconds:
            return REASON_BUDGET
        return None

    def note_skipped(self, reason: str) -> None:
        if reason == REASON_BREAKER:
            self.skipped_breaker += 1
        else:
            self.skipped_budget += 1

    def add_elapsed(self, seconds: float) -> None:
        self.lookups += 1
        self.spent_seconds += max(seconds, 0.0)

    def record_answer(self) -> None:
        """The registry answered (any status the retry loop treats as final)."""
        self.consecutive_failures = 0

    def record_transport_failure(self) -> None:
        self.transport_failures += 1
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.breaker_threshold:
            self._tripped = True

    @property
    def skipped(self) -> int:
        return self.skipped_budget + self.skipped_breaker

    def summary(self) -> dict[str, object]:
        """The record stored under ``scan_metadata['license_enrichment']``."""
        return {
            "budget_seconds": self.budget_seconds,
            "spent_seconds": round(self.spent_seconds, 1),
            "lookups": self.lookups,
            "lookup_failures": self.transport_failures,
            "not_looked_up": self.skipped,
            "not_looked_up_reason": (
                REASON_BREAKER
                if self.skipped_breaker and not self.skipped_budget
                else REASON_BUDGET
                if self.skipped_budget and not self.skipped_breaker
                else "both"
                if self.skipped
                else None
            ),
        }


_ACTIVE: ContextVar[EnrichmentBudget | None] = ContextVar("license_enrichment_budget", default=None)


def active_budget() -> EnrichmentBudget | None:
    return _ACTIVE.get()


@contextmanager
def enrichment_budget(budget: EnrichmentBudget) -> Iterator[EnrichmentBudget]:
    """Make *budget* the one :func:`base.request_with_retry` reports into."""
    token = _ACTIVE.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE.reset(token)
