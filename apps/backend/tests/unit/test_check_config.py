# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""`scripts.check_config` must actually catch a bad accessor, not just run.

The incident this script exists to prevent (a template `SECRET_KEY` passing
the health check and the install smoke test, then 500ing every authenticated
call once a lazy accessor finally read it) is reproduced directly here: set
the exact template value in a non-dev `APP_ENV` and assert the preflight
fails loudly, by name, rather than exiting 0.
"""

from __future__ import annotations

import pytest

from scripts import check_config


def test_every_accessor_is_zero_or_default_argument() -> None:
    """The discovery filter must not silently drop a real env accessor.

    A regression here (e.g. tightening the filter to also skip keyword-only
    parameters) would shrink coverage without any test noticing, since the
    remaining accessors would still all pass. Pinning the count catches that:
    it fails the moment discovery finds fewer than it used to.
    """
    accessors = check_config._accessors()
    names = {name for name, _ in accessors}

    # Spot-check a few that must always be discovered: the ones the old
    # ad-hoc lifespan checks already called by hand.
    assert "secret_key" in names
    assert "api_key_hmac_secret" in names
    assert "validate_demo_sandbox_limits" in names
    # A parameterised lookup must NOT be discovered as a bare accessor.
    assert "vuln_sla_days" not in names
    assert len(accessors) > 150


def test_passes_against_a_normal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    assert check_config.run(quiet=True) == 0


def test_catches_the_template_secret_key_incident(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exact incident `main.py`'s lifespan comment describes.

    A template `SECRET_KEY` in a non-dev `APP_ENV` must fail this preflight
    by name, not exit 0 and defer the failure to whichever request first
    exercises the accessor lazily.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "SECRET_KEY", "change-this-to-a-random-secret-key-min-32-chars"
    )

    exit_code = check_config.run(quiet=True)

    assert exit_code == 1
    printed = capsys.readouterr().out
    assert "secret_key" in printed
    assert "template value" in printed
