# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Reading a project's shared secret back out of the column (E22a).

The gateway used to hold the plaintext and compare it. It holds ciphertext
now, so there is a step between the row and the comparison that can fail, and
this covers what happens when it does.

Why it gets its own file rather than a case in the webhook suites: what is
being tested is a decision about a failure, not a delivery. The two situations
that collapse into "no secret" have to stay indistinguishable to the caller
and distinguishable in the log, and asserting both of those inside a delivery
test would bury them.
"""

from __future__ import annotations

import uuid

import structlog

from core.crypto import encrypt_secret
from services.webhook_service import _readable_webhook_secret


class _Project:
    def __init__(self, stored: str | None) -> None:
        self.id = uuid.uuid4()
        self.webhook_secret_encrypted = stored


def test_a_readable_secret_comes_back() -> None:
    """The ordinary case, present so the failures below mean something.

    Without it, a function that returned None unconditionally would satisfy
    every other assertion in this file.
    """
    project = _Project(encrypt_secret("the-shared-secret"))
    assert _readable_webhook_secret(project, delivery_id="d1") == "the-shared-secret"


def test_a_project_with_no_secret_yields_none() -> None:
    assert _readable_webhook_secret(_Project(None), delivery_id="d2") is None


def test_ciphertext_written_under_another_key_yields_none() -> None:
    """A key change must refuse deliveries, not accept them.

    The failure to avoid is an except branch that falls through and hands the
    caller the ciphertext, which would then be compared against the token an
    attacker chose. Returning the unreadable bytes as if they were the secret
    turns an operational problem into an authentication one.
    """
    from cryptography.fernet import Fernet

    stranger = Fernet(Fernet.generate_key())
    foreign = stranger.encrypt(b"the-shared-secret").decode()

    assert _readable_webhook_secret(_Project(foreign), delivery_id="d3") is None


def test_the_key_change_is_distinguishable_in_the_log() -> None:
    """Same answer to the caller, different entry in the record.

    A deployment whose key moved has every delivery for every affected project
    refused, and the fix is to re-issue. A project that was never activated
    needs somebody to finish setting it up. Answering both the same way is
    deliberate; logging both the same way would leave an operator with no way
    to tell which they have.

    ``capture_logs`` rather than ``caplog``: structlog writes its own stream,
    and an assertion on ``caplog.text`` passes or fails on which logger the
    event went through rather than on whether it was emitted. The first draft
    of this used ``caplog``, saw an empty record, and would have read as "the
    event is missing" while the event was on stdout.
    """
    from cryptography.fernet import Fernet

    foreign = Fernet(Fernet.generate_key()).encrypt(b"s").decode()

    with structlog.testing.capture_logs() as captured:
        _readable_webhook_secret(_Project(foreign), delivery_id="d4")
    assert any(e.get("event") == "webhook.secret_undecryptable" for e in captured), (
        captured
    )

    with structlog.testing.capture_logs() as captured:
        _readable_webhook_secret(_Project(None), delivery_id="d5")
    assert not any(
        e.get("event") == "webhook.secret_undecryptable" for e in captured
    ), (
        "a project that was never activated is reported as a key problem, so "
        "an operator chasing the log looks for a rotation that never happened"
    )


def test_the_log_carries_no_ciphertext() -> None:
    """The bytes that could not be read do not go into the record of that.

    Ciphertext is not plaintext, but it is credential material: it is exactly
    what somebody holding the old key would need. Logs travel further than the
    database does.
    """
    from cryptography.fernet import Fernet

    foreign = Fernet(Fernet.generate_key()).encrypt(b"the-shared-secret").decode()

    with structlog.testing.capture_logs() as captured:
        _readable_webhook_secret(_Project(foreign), delivery_id="d6")

    assert captured, "nothing was logged, so this asserts against an empty string"
    assert foreign not in repr(captured)
