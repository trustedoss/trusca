# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``configure_logging`` must be idempotent in fact, not just in its docstring.

The defect this pins was invisible in every per-module test and only appeared
as a cross-file ordering failure: three cross-team authorization tests asserted
on ``structlog.testing.capture_logs()`` and captured nothing, but ONLY when an
earlier test had both used the logger under test and booted the app a second
time.

The mechanism is ``cache_logger_on_first_use=True``. A module-level
``log = structlog.get_logger(...)`` freezes its bound logger on first use;
``structlog.configure()`` then swaps the global config without reaching into
that frozen proxy, so the logger keeps running a chain that ``capture_logs``
cannot replace. Every call to ``configure_logging`` after the first one puts
some other module in that state — which is why the second call has to be a
no-op rather than a repeat.
"""

from __future__ import annotations

import logging

import pytest
import structlog

import core.logging as logging_module
from core.logging import configure_logging


def _reset_module_state() -> None:
    """Forget the process-wide "already configured" marker.

    Tests in this file deliberately drive the first-call path, which the rest
    of the suite has usually already consumed.
    """
    logging_module._CONFIGURED_LEVEL = None


def test_capture_logs_still_works_after_a_second_boot() -> None:
    """The regression itself: log, boot again, then try to capture."""
    _reset_module_state()

    log = structlog.get_logger("test.logging.second_boot")
    configure_logging()
    # First use freezes this proxy's bound logger.
    log.warning("warm-up")
    # A second app / worker boot. Before the fix this replaced the global
    # config and left the frozen proxy unreachable.
    configure_logging()

    with structlog.testing.capture_logs() as captured:
        log.warning("authz.cross_team_attempt", resource="unit-test")

    assert [event["event"] for event in captured] == ["authz.cross_team_attempt"]


def test_same_level_reconfigure_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second call must not reach ``structlog.configure`` at all.

    Asserting on the resulting config would not catch this: re-applying the
    same settings produces an equal config while still freezing out every
    proxy that had already been used. What matters is that the call does not
    happen.
    """
    _reset_module_state()
    configure_logging("INFO")

    calls = 0
    real_configure = structlog.configure

    def _counting_configure(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        real_configure(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(structlog, "configure", _counting_configure)
    configure_logging("INFO")

    assert calls == 0, (
        "configure_logging re-ran at the same level — the frozen-proxy problem "
        "this guard exists for is back"
    )


def test_a_different_level_is_still_honoured() -> None:
    """Idempotence must not mean "ignore a real change of intent"."""
    _reset_module_state()

    configure_logging("INFO")
    configure_logging("DEBUG")

    assert logging_module._CONFIGURED_LEVEL == logging.DEBUG
    # Restore the suite's ambient level so later tests see what they expect.
    configure_logging("INFO")


def test_logger_cache_is_off_under_pytest() -> None:
    """The second half of the fix, and the one that survives call ordering.

    Making ``configure_logging`` idempotent stops it from freezing proxies on a
    repeat call, but any OTHER ``structlog.configure()`` — including the one
    inside ``capture_logs`` itself — can still do it. Under test we take the
    caching out of the picture entirely so a logging assertion cannot depend on
    what ran before it.
    """
    _reset_module_state()
    configure_logging()

    assert structlog.get_config()["cache_logger_on_first_use"] is False
