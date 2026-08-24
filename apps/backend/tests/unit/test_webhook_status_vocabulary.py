# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Contract test: the webhook status vocabulary exists in two places at once.

``WEBHOOK_STATUSES`` is what the service can return. The routers repeat the same
set in their OpenAPI ``responses`` description, because that is what an operator
reads before writing anything against the endpoint. CLAUDE.md hardening rule 2
requires a consistency test whenever one vocabulary lives in two places.

It has already drifted once in the direction this guards: the service reported a
skipped scan as ``duplicate``, which the documented vocabulary defined as "we
have seen this delivery" — so the docs and the code used one word for two
different events, and an operator reading a delivery log could not tell that a
push had gone unscanned.
"""

from __future__ import annotations

import re
from types import ModuleType

from api.v1.webhooks import github as github_router
from api.v1.webhooks import gitlab as gitlab_router
from services.webhook_service import WEBHOOK_STATUSES


def _documented_statuses(router_module: ModuleType) -> set[str]:
    """Extract the quoted status literals from the router's 200 description."""
    routes = [
        r for r in router_module.router.routes if getattr(r, "methods", None) == {"POST"}
    ]
    assert len(routes) == 1, "expected exactly one POST route per webhook router"
    description = routes[0].responses[200]["description"]
    # The description writes them as \"enqueued\"|\"duplicate\"|... — pull every
    # quoted token out of the status union and ignore the rest of the shape.
    union = description.split('"status": ', 1)[1].split(", ", 1)[0]
    return set(re.findall(r'"([a-z_]+)"', union))


def test_github_router_documents_every_status() -> None:
    assert _documented_statuses(github_router) == set(WEBHOOK_STATUSES)


def test_gitlab_router_documents_every_status() -> None:
    assert _documented_statuses(gitlab_router) == set(WEBHOOK_STATUSES)


def test_the_two_routers_agree_with_each_other() -> None:
    """Both endpoints are one contract; a status added to one must reach both."""
    assert _documented_statuses(github_router) == _documented_statuses(gitlab_router)


def test_outcome_vocabulary_is_the_status_set_minus_duplicate_plus_async_only() -> None:
    """What a delivery row can record, versus what a request can answer.

    ``duplicate`` describes this request ("we have seen this delivery"), not
    how the delivery ended. Recording it would overwrite the ending the row
    earned on its first pass, which is the one an operator asking "did this
    push get scanned" needs.

    S7 (concurrency-scaling-plan-2026-08-22.md §3.2/§4) adds one outcome the
    OTHER direction: ``capacity_retry_exhausted`` is written by
    ``tasks.webhook_capacity_retry``, asynchronously, well after the
    synchronous HTTP response was sent - so it is a valid ``outcome`` but
    never a live ``WebhookProcessResult.status`` / router-documented value.
    ``_ASYNC_ONLY_OUTCOMES`` names that one-way delta explicitly rather than
    letting the two sets silently diverge.
    """
    from services.webhook_service import (
        _ASYNC_ONLY_OUTCOMES,
        WEBHOOK_OUTCOMES,
        WEBHOOK_STATUSES,
    )

    assert WEBHOOK_OUTCOMES == (WEBHOOK_STATUSES - {"duplicate"}) | _ASYNC_ONLY_OUTCOMES
    assert "duplicate" not in WEBHOOK_OUTCOMES
    # The async-only outcomes must never leak into the synchronous status set
    # this test file's other cases hold the routers' OpenAPI docs to.
    assert _ASYNC_ONLY_OUTCOMES.isdisjoint(WEBHOOK_STATUSES)


def test_superseparable_outcomes_are_outcomes() -> None:
    """A redelivery can only supersede a value the column can actually hold."""
    from services.webhook_service import _SUPERSEDABLE_OUTCOMES, WEBHOOK_OUTCOMES

    assert _SUPERSEDABLE_OUTCOMES <= WEBHOOK_OUTCOMES
    # The two capacity skips, plus S7's own exhausted-retry outcome, and
    # nothing else: an ignored event will be ignored again, and an active
    # scan on the ref already covers the commit. capacity_retry_exhausted is
    # supersedable for the same reason the two skips are - the automatic
    # retry gave up, but the transient condition may have cleared since, and
    # a manual redelivery must still be able to recover the push.
    assert _SUPERSEDABLE_OUTCOMES == {
        "skipped_team_at_capacity",
        "skipped_disk_full",
        "capacity_retry_exhausted",
    }


def test_outcome_values_fit_the_column() -> None:
    """``webhook_deliveries.outcome`` is String(32)."""
    from sqlalchemy import String

    from models.api_key import WebhookDelivery
    from services.webhook_service import WEBHOOK_OUTCOMES

    column_type = WebhookDelivery.__table__.c.outcome.type
    assert isinstance(column_type, String)
    width = column_type.length
    assert width is not None
    for value in WEBHOOK_OUTCOMES:
        assert len(value) <= width, f"{value} is wider than outcome({width})"
