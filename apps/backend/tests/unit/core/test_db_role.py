# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The role verdict (ER49).

The logic is a pure function precisely so every combination can be stated
here. The check it replaced could not be tested this way: it read env vars and
raised inside the lifespan, so the only way to ask "what does it do when the
operator never configured separation" was to boot the whole app, which is why
nobody asked.
"""

from __future__ import annotations

import pytest

from core.db_role import evaluate_db_role, require_db_role_separation


def test_a_dml_only_role_is_reported_as_separated() -> None:
    v = evaluate_db_role(role="trustedoss_app", holds_ddl=False, strict=False)
    assert v.fatal is False
    assert v.level == "info"
    assert v.event == "db.role.separation.active"


def test_a_dml_only_role_satisfies_strict_mode() -> None:
    """Strict mode must not refuse the configuration it exists to require."""
    v = evaluate_db_role(role="trustedoss_app", holds_ddl=False, strict=True)
    assert v.fatal is False


def test_a_dml_only_role_is_recognised_whatever_it_is_called() -> None:
    """The reason this asks about privileges and not about a name.

    A Kubernetes install pointed at an external database names its DML-only
    role whatever the DBA named it. Comparing against 'trustedoss_app' would
    call this deployment unseparated and, in strict mode, refuse to start it.
    """
    v = evaluate_db_role(role="k8s_runtime", holds_ddl=False, strict=True)
    assert v.fatal is False
    assert v.event == "db.role.separation.active"


def test_a_privileged_role_warns_but_still_boots_by_default() -> None:
    """The regression that motivated ER49.

    A single-role install is supported, and the check this replaced refused to
    start one whenever APP_ENV was prod.
    """
    v = evaluate_db_role(role="trustedoss", holds_ddl=True, strict=False)
    assert v.fatal is False
    assert v.level == "warning"
    assert v.event == "db.role.separation.missing"


def test_the_warning_tells_the_operator_what_to_do() -> None:
    """The old message sent operators to inspect an L1 split they had never
    configured. Naming the condition without naming the remedy repeats that."""
    v = evaluate_db_role(role="trustedoss", holds_ddl=True, strict=False)
    # what is true
    assert "audit_logs" in v.message
    # that this is not necessarily a fault
    assert "supported configuration" in v.message
    # how to change it, on both deployment paths
    assert "POSTGRES_APP_PASSWORD" in v.message
    assert "ownerUrl" in v.message
    # how to make it enforced
    assert "REQUIRE_DB_ROLE_SEPARATION" in v.message


def test_strict_mode_turns_the_warning_into_a_refusal() -> None:
    v = evaluate_db_role(role="trustedoss", holds_ddl=True, strict=True)
    assert v.fatal is True
    assert v.level == "error"


def test_an_unanswerable_probe_does_not_stop_a_default_boot() -> None:
    """Not being able to tell is not a reason to refuse service."""
    v = evaluate_db_role(role="trustedoss", holds_ddl=None, strict=False)
    assert v.fatal is False
    assert v.level == "warning"


def test_an_unanswerable_probe_fails_closed_under_strict_mode() -> None:
    """Deliberate. The operator declared the split mandatory here, so
    "cannot confirm" must not pass the gate they asked for, or the gate
    becomes advisory exactly when something is already wrong."""
    v = evaluate_db_role(role="trustedoss", holds_ddl=None, strict=True)
    assert v.fatal is True
    assert "could not be determined" in v.message


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " True "])
def test_strict_mode_accepts_the_usual_truthy_spellings(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REQUIRE_DB_ROLE_SEPARATION", raw)
    assert require_db_role_separation() is True


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "maybe"])
def test_strict_mode_is_off_for_anything_else(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REQUIRE_DB_ROLE_SEPARATION", raw)
    assert require_db_role_separation() is False


def test_strict_mode_is_off_when_unset() -> None:
    """Default off: a single-role install must keep booting."""
    import os

    os.environ.pop("REQUIRE_DB_ROLE_SEPARATION", None)
    assert require_db_role_separation() is False
