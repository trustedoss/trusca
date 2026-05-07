"""
APP_ENV guard for ``scripts/seed_e2e_user.py --super-admin`` —
security-reviewer F8 (CWE-489 Active Debug Code).

The seed script's ``--super-admin`` flag writes ``is_superuser=True`` directly
via the seed helper. Convenience scripts that can mint privileged users are
benign in dev/test/ci but a footgun in prod: the on-call runs the script by
accident, a super-admin appears out of band, and the audit trail has no
human-attributable origin event.

The fix: the script reads ``os.getenv("APP_ENV")`` at runtime and refuses to
proceed when ``--super-admin`` is requested outside the allow-list ``{dev,
test, ci}``. The default (unset) refuses — explicit opt-in only.

These tests pin the contract by importing the helper directly (no subprocess
overhead) and exercising the guard via ``monkeypatch.setenv`` for each env
shape.
"""

from __future__ import annotations

import pytest


def test_super_admin_guard_allows_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    from scripts.seed_e2e_user import _refuse_super_admin_outside_safe_env

    # Must NOT raise.
    _refuse_super_admin_outside_safe_env()


@pytest.mark.parametrize("env_value", ["dev", "test", "ci", " DEV ", "Test", "CI"])
def test_super_admin_guard_allows_safe_envs(
    monkeypatch: pytest.MonkeyPatch, env_value: str
) -> None:
    """All three allowed envs work, case-insensitive + whitespace-tolerant."""
    monkeypatch.setenv("APP_ENV", env_value)
    from scripts.seed_e2e_user import _refuse_super_admin_outside_safe_env

    _refuse_super_admin_outside_safe_env()


@pytest.mark.parametrize(
    "env_value",
    [
        "production",
        "prod",
        "staging",
        "preprod",
        "demo",
        "qa",
        "release",
        # Adversarial / typo-shaped values.
        "dev,prod",  # comma injection
        "dev prod",  # space injection
        "dev\nprod",  # newline injection
        "dev'OR'1=1",  # SQL keyword
        "javascript:alert(1)",  # script scheme
        "‮dev",  # RTL override
        # Note: actual null bytes ('\x00') cannot be set via os.environ on
        # POSIX (the syscall rejects them) and the OS would never deliver
        # one to the process. The "set with null in the middle" attack
        # therefore cannot reach this codepath.
        "",  # empty string ≠ unset
    ],
)
def test_super_admin_guard_refuses_unsafe_env(
    monkeypatch: pytest.MonkeyPatch, env_value: str
) -> None:
    monkeypatch.setenv("APP_ENV", env_value)
    from scripts.seed_e2e_user import _refuse_super_admin_outside_safe_env

    with pytest.raises(SystemExit) as exc_info:
        _refuse_super_admin_outside_safe_env()
    # Exit code 1 = refused (vs. 2 = arg error).
    assert exc_info.value.code == 1


def test_super_admin_guard_refuses_unset_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset APP_ENV → refuse. Forgotten-env footgun is the primary case."""
    monkeypatch.delenv("APP_ENV", raising=False)
    from scripts.seed_e2e_user import _refuse_super_admin_outside_safe_env

    with pytest.raises(SystemExit) as exc_info:
        _refuse_super_admin_outside_safe_env()
    assert exc_info.value.code == 1


def test_super_admin_guard_message_mentions_allow_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refusal stderr message includes the allow-list — operators get a hint."""
    monkeypatch.setenv("APP_ENV", "production")
    from scripts.seed_e2e_user import _refuse_super_admin_outside_safe_env

    with pytest.raises(SystemExit):
        _refuse_super_admin_outside_safe_env()
    captured = capsys.readouterr()
    assert "Refusing" in captured.err
    assert "dev" in captured.err  # at least one allowed env named
    assert "production" in captured.err  # the offending value is shown


def test_super_admin_guard_runtime_env_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The helper MUST read APP_ENV at call time (CLAUDE.md core rule #11).
    Mutating after the module import must change the decision.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    from scripts.seed_e2e_user import _refuse_super_admin_outside_safe_env

    # First call: dev → allowed.
    _refuse_super_admin_outside_safe_env()
    # Re-set to production: must now refuse on the SAME imported helper.
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(SystemExit):
        _refuse_super_admin_outside_safe_env()
