"""
Marathon bundle 2 (D1) — ``scripts/seed_e2e_user.py --no-password`` validation.

The OAuth-only fixture (``--no-password``) MUST be paired with
``--with-oauth-identity`` or the seeded user has zero authentication
methods and becomes unrecoverable. The seed helper refuses with a
``ValueError`` before any DB work happens, so the caller sees a clean
error instead of a NOT-NULL / FK surprise after the org+team are
already committed.

These tests pin the precondition by importing ``_seed`` directly and
asserting it raises BEFORE any engine is opened. The CLI wrapper
``main()`` translates that ValueError to exit code 2 (validation failure)
distinct from generic exit code 1 (runtime failure).
"""

from __future__ import annotations

import asyncio

import pytest


def _run_seed(**kwargs: object) -> None:
    """Helper that drives the async ``_seed`` to completion synchronously."""
    from scripts.seed_e2e_user import _seed

    asyncio.run(_seed(**kwargs))  # type: ignore[arg-type]


def test_no_password_requires_oauth_identity() -> None:
    """``--no-password`` without ``--with-oauth-identity`` raises ValueError.

    The check fires BEFORE any engine / session is constructed so the
    DATABASE_URL env doesn't need to be set for this test. Pinning the
    raise location matters because the test runs without a Postgres
    fixture — if the validation moved past the engine creation we'd
    silently start to ``skip`` instead of ``fail``.
    """
    with pytest.raises(ValueError) as excinfo:
        _run_seed(
            project_names=["test"],
            email=None,
            password=None,
            with_scan=False,
            component_count=0,
            component_prefix="comp",
            no_password=True,
            with_oauth_identity=None,
        )
    msg = str(excinfo.value).lower()
    assert "no-password" in msg or "no_password" in msg
    assert "with-oauth-identity" in msg or "with_oauth_identity" in msg


def test_no_password_with_oauth_identity_passes_validation() -> None:
    """``--no-password --with-oauth-identity github`` passes the precondition.

    The call still fails downstream because we don't have a Postgres
    fixture wired in this unit test; the assertion is that we get past
    the precondition check and hit a connection-related failure (any
    exception that is NOT the precondition ValueError).
    """
    try:
        _run_seed(
            project_names=["test"],
            email=None,
            password=None,
            with_scan=False,
            component_count=0,
            component_prefix="comp",
            no_password=True,
            with_oauth_identity="github",
        )
    except ValueError as exc:
        if "no-password" in str(exc).lower():
            pytest.fail(
                f"precondition fired unexpectedly when both flags supplied: {exc}"
            )
        # Any other ValueError is a downstream issue (e.g. invalid project
        # name parsing) — re-raise.
        raise
    except Exception:
        # Any non-ValueError is a downstream connection / engine failure
        # which is expected in the unit-test environment without a live
        # Postgres. The precondition test passed.
        pass


def test_no_password_main_exit_code_2_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main()`` translates the ValueError to exit code 2 (vs. 1 for runtime)."""
    monkeypatch.setattr("sys.argv", [
        "seed_e2e_user.py",
        "--project-names", "test",
        "--no-password",
    ])
    from scripts.seed_e2e_user import main

    rc = main()
    assert rc == 2, f"expected exit code 2 (validation), got {rc}"
    captured = capsys.readouterr()
    assert "precondition" in captured.err.lower()
    assert "with-oauth-identity" in captured.err
