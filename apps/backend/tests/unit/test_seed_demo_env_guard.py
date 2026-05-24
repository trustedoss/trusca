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


def test_resolve_demo_password_generates_random_in_dev(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """In *dev only*, a random password is generated and printed once.

    dev is a single-developer machine, so printing the plaintext for local
    convenience is acceptable (CLAUDE.md §5 targets shared/retained log sinks).
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("DEMO_SUPER_ADMIN_PASSWORD", raising=False)
    from scripts.seed_demo import _resolve_demo_password

    pw = _resolve_demo_password()
    assert len(pw) >= 24  # token_urlsafe(18) yields 24+ chars
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["event"] == "seed_demo.generated_password"
    assert payload["password"] == pw


def test_resolve_demo_password_demo_never_logs_plaintext(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M-2: in the demo env the generated plaintext is NEVER logged.

    The Cloud Run reset Job runs with APP_ENV=demo every night, so any plaintext
    here would persist in Cloud Logging (CLAUDE.md §5 violation). We still
    generate a valid password (it is hashed into the user row) but only emit a
    masked advisory event — the value itself must not appear anywhere in stdout.
    """
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.delenv("DEMO_SUPER_ADMIN_PASSWORD", raising=False)
    from scripts.seed_demo import _resolve_demo_password

    pw = _resolve_demo_password()
    assert len(pw) >= 24  # still a strong random password
    out = capsys.readouterr().out
    # The plaintext password must NOT appear anywhere in the captured output.
    assert pw not in out
    payload = json.loads(out.strip())
    assert payload["event"] == "seed_demo.generated_password"
    assert payload["password"] == "***"  # masked, not the real value
    # The email local-part is masked (mask_pii keeps the domain, hides the
    # identity), and an advisory note points at the Secret Manager path.
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
