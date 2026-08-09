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
