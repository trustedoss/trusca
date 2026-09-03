# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Parsing the broker's own message envelopes (ER11).

The reaper reclaims a ``queued`` scan only when this parser fails to find its
task id in the broker, so a parser that silently returns ``None`` for a real
envelope would make it kill live scans. The envelopes below are the shapes an
actual broker produced, captured by publishing through Celery 5.4 / kombu 5.6
and then taking delivery through kombu without acking, not written by hand
(hardening rule 3).
"""

from __future__ import annotations

import json

from tasks._broker_inventory import _task_id_from_envelope

TASK_ID = "1cfe7688-9970-432c-b99b-6694b7b0dbf6"

# As it sits in `LRANGE trustedoss.scan 0 -1`.
QUEUED_ENVELOPE = {
    "body": "W1siYWJjIl0sIHt9LCB7ImNhbGxiYWNrcyI6IG51bGx9XQ==",
    "content-encoding": "utf-8",
    "content-type": "application/json",
    "headers": {
        "lang": "py",
        "task": "trustedoss.scan_source",
        "id": TASK_ID,
        "root_id": TASK_ID,
        "parent_id": None,
        "group": None,
    },
    "properties": {
        "correlation_id": TASK_ID,
        "reply_to": "6d1f0e0b-4a3a-3f2e-9a5f-1d2c3b4a5e6f",
        "delivery_mode": 2,
        "delivery_info": {"exchange": "", "routing_key": "trustedoss.scan"},
        "priority": 0,
        "body_encoding": "base64",
        "delivery_tag": "8f0a1b2c-3d4e-5f60-7182-93a4b5c6d7e8",
    },
}

# As it sits in `HGETALL unacked`: the same envelope wrapped in a triple.
UNACKED_VALUE = [QUEUED_ENVELOPE, "", "trustedoss.scan"]


def test_reads_the_id_from_a_queued_envelope() -> None:
    assert _task_id_from_envelope(json.dumps(QUEUED_ENVELOPE)) == TASK_ID


def test_reads_the_id_from_an_unacked_triple() -> None:
    """A reserved or running task lives here under task_acks_late, and missing
    it would mean reaping a scan that is on a worker right now."""
    assert _task_id_from_envelope(json.dumps(UNACKED_VALUE)) == TASK_ID


def test_falls_back_to_correlation_id_when_headers_lack_an_id() -> None:
    envelope = json.loads(json.dumps(QUEUED_ENVELOPE))
    del envelope["headers"]["id"]
    assert _task_id_from_envelope(json.dumps(envelope)) == TASK_ID


def test_unparseable_input_yields_no_id() -> None:
    # None means "this item told us nothing", which the caller treats as one
    # fewer known id, never as a licence to reap something else.
    assert _task_id_from_envelope("not json") is None
    assert _task_id_from_envelope(json.dumps({"headers": {}})) is None
    assert _task_id_from_envelope(json.dumps([])) is None
    assert _task_id_from_envelope(json.dumps("a string")) is None
    assert _task_id_from_envelope(None) is None


def test_an_id_that_is_not_a_string_is_ignored() -> None:
    envelope = json.loads(json.dumps(QUEUED_ENVELOPE))
    envelope["headers"]["id"] = 12345
    envelope["properties"]["correlation_id"] = ""
    assert _task_id_from_envelope(json.dumps(envelope)) is None
