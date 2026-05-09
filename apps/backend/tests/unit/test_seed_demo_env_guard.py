"""
APP_ENV guard + argparse smoke test for ``scripts/seed_demo.py`` —
Chore F (GCP Demo SaaS bundle).

The seed_demo script seeds an organization, a super-admin, and a realistic
demo dataset directly into Postgres. Like ``seed_e2e_user.py --super-admin``
it can mint a privileged user and is therefore footgun-prone if it ever ships
with a prod image and the on-call runs it by accident.

The fix mirrors the F8 pattern from ``seed_e2e_user.py``: read APP_ENV at
runtime and refuse outside ``{dev, demo}``.

These tests pin the contract by importing the helper directly (no
subprocess overhead) and exercising the guard via ``monkeypatch.setenv`` for
each env shape. The dry-run path also exercises ``main()`` without touching
the DB.
"""

from __future__ import annotations

import json

import pytest


def test_seed_demo_guard_allows_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "demo")
    from scripts.seed_demo import _refuse_outside_safe_env

    _refuse_outside_safe_env()


@pytest.mark.parametrize(
    "env_value",
    ["dev", "demo", " DEV ", "Demo", "DEV", " demo "],
)
def test_seed_demo_guard_allows_safe_envs(monkeypatch: pytest.MonkeyPatch, env_value: str) -> None:
    """Both allowed envs work, case-insensitive + whitespace-tolerant."""
    monkeypatch.setenv("APP_ENV", env_value)
    from scripts.seed_demo import _refuse_outside_safe_env

    _refuse_outside_safe_env()


@pytest.mark.parametrize(
    "env_value",
    [
        "production",
        "prod",
        "staging",
        "preprod",
        "test",  # explicitly excluded — pytest must not invoke seed_demo
        "ci",
        "qa",
        "release",
        # Adversarial / typo-shaped values.
        "demo,prod",
        "demo prod",
        "demo\nprod",
        "javascript:alert(1)",
        "‮demo",  # RTL override
        "",
    ],
)
def test_seed_demo_guard_refuses_unsafe_env(
    monkeypatch: pytest.MonkeyPatch, env_value: str
) -> None:
    monkeypatch.setenv("APP_ENV", env_value)
    from scripts.seed_demo import _refuse_outside_safe_env

    with pytest.raises(SystemExit) as exc_info:
        _refuse_outside_safe_env()
    assert exc_info.value.code == 1


def test_seed_demo_guard_refuses_unset_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset APP_ENV → refuse. Forgotten-env footgun is the primary case."""
    monkeypatch.delenv("APP_ENV", raising=False)
    from scripts.seed_demo import _refuse_outside_safe_env

    with pytest.raises(SystemExit) as exc_info:
        _refuse_outside_safe_env()
    assert exc_info.value.code == 1


def test_seed_demo_guard_message_mentions_allow_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refusal stderr message includes the allow-list — operators get a hint."""
    monkeypatch.setenv("APP_ENV", "production")
    from scripts.seed_demo import _refuse_outside_safe_env

    with pytest.raises(SystemExit):
        _refuse_outside_safe_env()
    captured = capsys.readouterr()
    assert "Refusing" in captured.err
    assert "demo" in captured.err  # allow-list named
    assert "production" in captured.err  # offending value shown


def test_seed_demo_guard_runtime_env_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard MUST read APP_ENV at call time (CLAUDE.md core rule #11)."""
    monkeypatch.setenv("APP_ENV", "demo")
    from scripts.seed_demo import _refuse_outside_safe_env

    _refuse_outside_safe_env()
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(SystemExit):
        _refuse_outside_safe_env()


def test_seed_demo_main_dry_run_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main(--dry-run)`` validates env + emits the JSON contract without DB."""
    monkeypatch.setenv("APP_ENV", "demo")
    from scripts.seed_demo import main

    rc = main(["--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["users"] == []
    assert payload["projects"] == []


def test_seed_demo_main_dry_run_refuses_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard runs even in --dry-run so the refusal path is exercised."""
    monkeypatch.setenv("APP_ENV", "production")
    from scripts.seed_demo import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--dry-run"])
    assert exc_info.value.code == 1
