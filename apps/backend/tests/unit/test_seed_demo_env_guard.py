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


# ---------------------------------------------------------------------------
# Chore O / M2 — Demo super-admin password rotation
# ---------------------------------------------------------------------------


def test_resolve_demo_password_uses_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit DEMO_SUPER_ADMIN_PASSWORD env is used verbatim."""
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("DEMO_SUPER_ADMIN_PASSWORD", "Hunter2-MinLen12-OK!!")
    from scripts.seed_demo import _resolve_demo_password

    assert _resolve_demo_password() == "Hunter2-MinLen12-OK!!"


def test_resolve_demo_password_rejects_short_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit but short (< 12 chars) password is refused."""
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("DEMO_SUPER_ADMIN_PASSWORD", "tooshort")
    from scripts.seed_demo import _resolve_demo_password

    with pytest.raises(RuntimeError, match="at least 12 characters"):
        _resolve_demo_password()


def test_resolve_demo_password_dev_default_in_dev(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """W6-chore-seed A: dev env without the env var → fixed dev default.

    Replaces the previous "random token per run" behaviour that locked
    developers out the moment the seed log scrolled off the terminal. The
    fixed default survives re-seeds and is documented in [[project_demo_test_setup]].
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("DEMO_SUPER_ADMIN_PASSWORD", raising=False)
    from scripts.seed_demo import _DEV_DEMO_PASSWORD_DEFAULT, _resolve_demo_password

    pw = _resolve_demo_password()
    assert pw == _DEV_DEMO_PASSWORD_DEFAULT
    # Length policy: the dev default itself satisfies the 12-char minimum.
    assert len(pw) >= 12
    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert payload["event"] == "seed_demo.dev_default_password_applied"
    assert payload["app_env"] == "dev"
    # The plaintext must never appear in the advisory line (masked email +
    # advisory note only — the password lives only in the hashed user row).
    assert pw not in out
    assert "DEMO_SUPER_ADMIN_PASSWORD" in payload["note"]


def test_resolve_demo_password_dev_default_in_demo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """W6-chore-seed A: demo env without the env var → same fixed dev default.

    The demo Cloud Run Job SHOULD provision DEMO_SUPER_ADMIN_PASSWORD via
    Secret Manager (path 1), but if it doesn't, the fixed default beats the
    prior "random token per nightly Job" footgun that left the demo deploy
    in an unloginable state after the seed log rotated.
    """
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.delenv("DEMO_SUPER_ADMIN_PASSWORD", raising=False)
    from scripts.seed_demo import _DEV_DEMO_PASSWORD_DEFAULT, _resolve_demo_password

    pw = _resolve_demo_password()
    assert pw == _DEV_DEMO_PASSWORD_DEFAULT
    out = capsys.readouterr().out
    # The plaintext must NOT appear anywhere in the captured output
    # (CLAUDE.md §5 — Cloud Logging never sees the password).
    assert pw not in out
    payload = json.loads(out.strip())
    assert payload["event"] == "seed_demo.dev_default_password_applied"
    assert payload["app_env"] == "demo"
    # Masked email + advisory pointing at Secret Manager.
    assert payload["email"].startswith("ad***")
    assert "admin@demo.trustedoss.dev" not in payload["email"]
    assert "DEMO_SUPER_ADMIN_PASSWORD" in payload["note"]


def test_resolve_demo_password_explicit_demo_not_logged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicit (Secret Manager-backed) demo password is used and not logged.

    The recommended path: the operator provisions DEMO_SUPER_ADMIN_PASSWORD, so
    nothing is generated and nothing is printed at all.
    """
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("DEMO_SUPER_ADMIN_PASSWORD", "StableDemoPw-OK!")
    from scripts.seed_demo import _resolve_demo_password

    pw = _resolve_demo_password()
    assert pw == "StableDemoPw-OK!"
    out = capsys.readouterr().out
    assert pw not in out
    assert out.strip() == ""  # the explicit path is entirely silent


def test_resolve_demo_password_refuses_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production without an explicit env raises — never auto-generates."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DEMO_SUPER_ADMIN_PASSWORD", raising=False)
    from scripts.seed_demo import _resolve_demo_password

    with pytest.raises(RuntimeError, match="DEMO_SUPER_ADMIN_PASSWORD is required"):
        _resolve_demo_password()


def test_resolve_demo_password_runtime_env_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The function MUST read env vars at call time (CLAUDE.md core rule #11)."""
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("DEMO_SUPER_ADMIN_PASSWORD", "FirstPasswordOK")
    from scripts.seed_demo import _resolve_demo_password

    assert _resolve_demo_password() == "FirstPasswordOK"
    monkeypatch.setenv("DEMO_SUPER_ADMIN_PASSWORD", "SecondPasswordOK")
    assert _resolve_demo_password() == "SecondPasswordOK"
