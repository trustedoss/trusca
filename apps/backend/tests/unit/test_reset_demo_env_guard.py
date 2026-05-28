"""
APP_ENV guard + argparse smoke test for ``scripts/reset_demo.py`` — v2.1 B5.

``reset_demo.py`` is the DESTRUCTIVE half of the live-demo lifecycle: it drops
the demo dataset (demo-org + demo users) and reseeds via ``seed_demo._seed``.
Because it can delete data, the APP_ENV allow-list guard (``dev`` / ``demo``)
matters even more here than for the seed. These tests pin the guard and the
no-DB dry-run path by importing the helpers directly, mirroring
``test_seed_demo_env_guard.py``.
"""

from __future__ import annotations

import json

import pytest


def test_reset_demo_guard_allows_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "demo")
    from scripts import seed_demo

    # reset_demo reuses seed_demo's guard — exercise it through the module ref.
    seed_demo._refuse_outside_safe_env()


@pytest.mark.parametrize(
    "env_value",
    ["production", "prod", "staging", "test", "ci", "", "demo,prod"],
)
def test_reset_demo_main_dry_run_refuses_unsafe_env(
    monkeypatch: pytest.MonkeyPatch, env_value: str
) -> None:
    """The guard runs even in --dry-run so the destructive path can never start
    outside dev/demo."""
    monkeypatch.setenv("APP_ENV", env_value)
    from scripts.reset_demo import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--dry-run"])
    assert exc_info.value.code == 1


def test_reset_demo_main_dry_run_refuses_unset_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    from scripts.reset_demo import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--dry-run"])
    assert exc_info.value.code == 1


def test_reset_demo_main_dry_run_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main(--dry-run)`` validates env + emits the JSON contract without DB."""
    monkeypatch.setenv("APP_ENV", "demo")
    from scripts.reset_demo import main

    rc = main(["--dry-run"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["reset"] is True


def test_reset_demo_email_suffix_is_scoped() -> None:
    """The deletion filter must be tied to the stable demo email suffix so a
    real user can never be swept up by the reset."""
    from scripts import seed_demo
    from scripts.reset_demo import _DEMO_EMAIL_SUFFIX

    assert _DEMO_EMAIL_SUFFIX == "@demo.trustedoss.dev"
    # The seed's super-admin email lives under that suffix (cross-check the two
    # scripts agree on the demo identity).
    assert seed_demo._DEMO_SUPER_ADMIN_EMAIL.endswith(_DEMO_EMAIL_SUFFIX)
