"""
Phase F (release-diff e2e) — ``scripts/seed_e2e_user.py --scan-count`` validation.

``--scan-count`` seeds a second succeeded scan on the first project so the
diff endpoint has a deterministic base→target fixture. Only 1 or 2 are
accepted, and ``--scan-count 2`` needs a real "unchanged" component pool
(``--component-count >= 4``) so the diff can tell unchanged apart from the
removed / changed / added deltas.

These tests pin the preconditions by importing ``_seed`` directly and
asserting it raises BEFORE any engine is opened (mirroring the
``--no-password`` guard tests), plus the ``main()`` exit-code translation.
They run without a Postgres fixture — the raise fires before the engine is
constructed, so no DATABASE_URL is required.
"""

from __future__ import annotations

import asyncio

import pytest


def _run_seed(**kwargs: object) -> None:
    """Drive the async ``_seed`` to completion synchronously."""
    from scripts.seed_e2e_user import _seed

    asyncio.run(_seed(**kwargs))  # type: ignore[arg-type]


def test_scan_count_above_two_is_refused() -> None:
    """``--scan-count 3`` raises ValueError before any DB work.

    Values > 2 have no defined diff-delta semantics, so the seed refuses
    loudly rather than silently seeding an ambiguous fixture.
    """
    with pytest.raises(ValueError) as excinfo:
        _run_seed(
            project_names=["test"],
            email=None,
            password=None,
            with_scan=True,
            scan_count=3,
            component_count=6,
            component_prefix="comp",
        )
    assert "scan-count" in str(excinfo.value).lower()


def test_scan_count_two_requires_component_floor() -> None:
    """``--scan-count 2`` with ``--component-count < 4`` raises ValueError.

    scan1 needs an unchanged pool alongside the removed/changed/added
    deltas; fewer than four components cannot supply one deterministically.
    """
    with pytest.raises(ValueError) as excinfo:
        _run_seed(
            project_names=["test"],
            email=None,
            password=None,
            with_scan=True,
            scan_count=2,
            component_count=3,
            component_prefix="comp",
        )
    msg = str(excinfo.value).lower()
    assert "scan-count 2" in msg
    assert "component-count" in msg


def test_scan_count_one_is_the_unchanged_default() -> None:
    """``scan_count=1`` (the default) passes validation.

    It still fails downstream in this fixtureless unit env (no Postgres),
    but the assertion is that we get PAST the scan-count preconditions —
    i.e. no ``--scan-count`` ValueError is raised.
    """
    try:
        _run_seed(
            project_names=["test"],
            email=None,
            password=None,
            with_scan=True,
            scan_count=1,
            component_count=6,
            component_prefix="comp",
        )
    except ValueError as exc:
        if "scan-count" in str(exc).lower():
            pytest.fail(f"scan-count precondition fired for the default: {exc}")
        raise
    except Exception:
        # Any non-ValueError is the expected downstream engine/connection
        # failure without a live Postgres — the precondition passed.
        pass


def test_scan_count_two_passes_precondition_with_enough_components() -> None:
    """``--scan-count 2 --component-count 4`` clears the precondition gate.

    Fails downstream (no Postgres) but must NOT raise a scan-count
    ValueError — the floor is satisfied.
    """
    try:
        _run_seed(
            project_names=["test"],
            email=None,
            password=None,
            with_scan=True,
            scan_count=2,
            component_count=4,
            component_prefix="comp",
        )
    except ValueError as exc:
        if "scan-count" in str(exc).lower():
            pytest.fail(
                f"scan-count precondition fired despite a valid floor: {exc}"
            )
        raise
    except Exception:
        pass


def test_main_exit_code_2_on_scan_count_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main()`` translates the scan-count ValueError to exit code 2."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_e2e_user.py",
            "--project-names",
            "test",
            "--scan-count",
            "2",
            "--component-count",
            "3",
        ],
    )
    from scripts.seed_e2e_user import main

    rc = main()
    assert rc == 2, f"expected exit code 2 (validation), got {rc}"
    captured = capsys.readouterr()
    assert "precondition" in captured.err.lower()
    assert "component-count" in captured.err.lower()
