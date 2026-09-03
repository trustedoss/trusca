# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Which queues each worker kind subscribes to, agreed across all three sides.

Hardening rule 2: the same vocabulary lives in three places, so an equality
test is mandatory. The places are ``tests/contracts/queue-names.json``
(``worker_subscriptions``, the oracle), ``docker-compose.yml``'s
``WORKER_SCAN_QUEUES`` / ``WORKER_DEFAULT_QUEUES`` defaults, and
``charts/trustedoss/values.yaml``'s ``worker.subscriptions``.

Why this test exists at all (ER11). These used to be governed by a single
``transition_subscribe_both_queues`` boolean meaning "both kinds take both
queues", a deliberate window so a rolling upgrade could drain pre-split
messages. The window closed but the default did not change, and while it
stood the isolation the queue split exists for did not hold: a worker-default
subscribed to the scan queue can pick up an hour-long scan and the short,
frequent tasks it serves then queue behind it.

The asymmetry below is the point and is easy to "tidy" into symmetry by
accident, in one of the three files, without the other two noticing. That is
exactly the drift this repository has hit before: Compose and Helm disagreeing
means queue isolation exists or not depending on which way you deployed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPO_ROOT / "tests" / "contracts" / "queue-names.json"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
CHART_VALUES_PATH = REPO_ROOT / "charts" / "trustedoss" / "values.yaml"


def _contract() -> dict[str, list[str]]:
    data: dict[str, Any] = json.loads(CONTRACT_PATH.read_text())
    subs: dict[str, list[str]] = data["worker_subscriptions"]
    return subs


def _compose_default_queues(service: str, variable: str) -> list[str]:
    """The `-Q` default baked into a compose service's command.

    Read out of the raw command string rather than by starting the stack:
    what is under test is the DEFAULT an operator gets with no override, which
    is the `${VAR:-...}` fallback.
    """
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    command = str(compose["services"][service]["command"])
    match = re.search(rf"-Q \$\{{{variable}:-([^}}]+)\}}", command)
    assert match, f"no `-Q ${{{variable}:-...}}` default found in {service}'s command"
    return match.group(1).split(",")


def _chart_subscriptions() -> dict[str, list[str]]:
    values = yaml.safe_load(CHART_VALUES_PATH.read_text())
    subs: dict[str, list[str]] = values["worker"]["subscriptions"]
    return subs


def test_compose_matches_the_contract() -> None:
    contract = _contract()
    assert _compose_default_queues("worker-scan", "WORKER_SCAN_QUEUES") == contract["scan"]
    assert (
        _compose_default_queues("worker-default", "WORKER_DEFAULT_QUEUES")
        == contract["default"]
    )


def test_chart_matches_the_contract() -> None:
    contract = _contract()
    chart = _chart_subscriptions()
    assert chart["scan"] == contract["scan"]
    assert chart["default"] == contract["default"]


def test_the_default_worker_never_takes_the_scan_queue() -> None:
    """The property the split exists for, asserted on its own.

    Stated separately from the equality tests above so that widening it in all
    three files at once still fails, rather than passing because they agree.
    """
    contract = json.loads(CONTRACT_PATH.read_text())
    scan_queue = contract["scan_queue"]

    assert scan_queue not in contract["worker_subscriptions"]["default"]
    assert scan_queue not in _chart_subscriptions()["default"]
    assert scan_queue not in _compose_default_queues(
        "worker-default", "WORKER_DEFAULT_QUEUES"
    )


def test_every_queue_has_a_consumer() -> None:
    """Narrowing must never leave a queue with nothing listening to it."""
    contract = json.loads(CONTRACT_PATH.read_text())
    subscribed = {
        queue
        for queues in contract["worker_subscriptions"].values()
        for queue in queues
    }
    assert contract["scan_queue"] in subscribed
    assert contract["default_queue"] in subscribed
