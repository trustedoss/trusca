# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
APP_ENV guard + argparse + catalog-builder smoke tests for
``scripts/seed_load_test.py`` (concurrency-scaling plan, unit 13 / M3).

Mirrors ``test_seed_demo_env_guard.py``'s shape for the same reason: this
script can create tens of thousands of rows, so the "did I mean to run this
here" guard is the part that most needs a fast, DB-free regression net.
``_build_catalog`` (the in-memory row-shape builder: no DB access) gets its
own coverage here too, since the DB-touching half of the script
(``_seed``) is exercised separately by
``tests/integration/test_seed_load_test_db.py`` against the real Postgres.
"""

from __future__ import annotations

import json

import pytest


def test_guard_allows_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    from scripts.seed_load_test import _refuse_outside_safe_env

    _refuse_outside_safe_env()


@pytest.mark.parametrize("env_value", ["dev", " DEV ", "Dev", " dev "])
def test_guard_allows_dev_case_and_whitespace_tolerant(
    monkeypatch: pytest.MonkeyPatch, env_value: str
) -> None:
    monkeypatch.setenv("APP_ENV", env_value)
    from scripts.seed_load_test import _refuse_outside_safe_env

    _refuse_outside_safe_env()


@pytest.mark.parametrize(
    "env_value",
    [
        "demo",  # stricter than seed_demo.py: demo is NOT allowed here
        "production",
        "prod",
        "staging",
        "test",
        "ci",
        "",
    ],
)
def test_guard_refuses_everything_except_dev(
    monkeypatch: pytest.MonkeyPatch, env_value: str
) -> None:
    monkeypatch.setenv("APP_ENV", env_value)
    from scripts.seed_load_test import _refuse_outside_safe_env

    with pytest.raises(SystemExit) as exc_info:
        _refuse_outside_safe_env()
    assert exc_info.value.code == 1


def test_guard_refuses_unset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    from scripts.seed_load_test import _refuse_outside_safe_env

    with pytest.raises(SystemExit) as exc_info:
        _refuse_outside_safe_env()
    assert exc_info.value.code == 1


def test_guard_message_mentions_dev_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "demo")
    from scripts.seed_load_test import _refuse_outside_safe_env

    with pytest.raises(SystemExit):
        _refuse_outside_safe_env()
    captured = capsys.readouterr()
    assert "Refusing" in captured.err
    assert "dev" in captured.err
    assert "demo" in captured.err  # offending value shown


def test_guard_runtime_env_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Must read APP_ENV at call time (CLAUDE.md core rule #11)."""
    monkeypatch.setenv("APP_ENV", "dev")
    from scripts.seed_load_test import _refuse_outside_safe_env

    _refuse_outside_safe_env()
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(SystemExit):
        _refuse_outside_safe_env()


def test_main_dry_run_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    from scripts.seed_load_test import main

    rc = main(["--dry-run"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["organization_id"] is None


def test_main_dry_run_refuses_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    from scripts.seed_load_test import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--dry-run"])
    assert exc_info.value.code == 1


def test_main_dry_run_reports_requested_scale(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    from scripts.seed_load_test import main

    rc = main(
        ["--dry-run", "--projects", "5", "--scans-per-project", "3", "--components-per-scan", "20"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["projects"] == 5
    assert payload["scans_per_project"] == 3
    assert payload["components_per_scan"] == 20


def test_parse_args_defaults_match_the_plan() -> None:
    """The concurrency-scaling plan's M3 row names 200x20x500 explicitly."""
    from scripts.seed_load_test import _parse_args

    args = _parse_args([])
    assert args.projects == 200
    assert args.scans_per_project == 20
    assert args.components_per_scan == 500
    assert args.reset is False
    assert args.dry_run is False


def test_parse_args_rejects_degenerate_scale() -> None:
    from scripts.seed_load_test import _parse_args

    with pytest.raises(SystemExit):
        _parse_args(["--projects", "0"])
    with pytest.raises(SystemExit):
        _parse_args(["--scans-per-project", "0"])
    with pytest.raises(SystemExit):
        _parse_args(["--components-per-scan", "3"])  # needs room for 5 common + 1 long-tail


def test_parse_args_reset_flag() -> None:
    from scripts.seed_load_test import _parse_args

    assert _parse_args([]).reset is False
    assert _parse_args(["--reset"]).reset is True


# ---------------------------------------------------------------------------
# _build_catalog: pure in-memory row builder, no DB
# ---------------------------------------------------------------------------


def test_build_catalog_row_counts_match_requested_scale() -> None:
    from datetime import UTC, datetime

    from scripts.seed_load_test import COMMON_COMPONENT_NAMES, _build_catalog

    now = datetime.now(tz=UTC)
    component_rows, version_rows, project_catalog = _build_catalog(
        project_count=4, components_per_scan=10, now=now
    )
    # Every project's per-scan catalog is exactly components_per_scan long:
    # this is what makes scan_components_total == projects * scans * 10 an
    # EXACT equation rather than an estimate (module docstring's headline
    # claim).
    assert all(len(catalog) == 10 for catalog in project_catalog)
    assert len(project_catalog) == 4
    # 5 common (shared) + 1 rare (shared, project 0 only) + long-tail per
    # project = total distinct components created.
    assert len(component_rows) == len(version_rows)
    common_names_in_project_zero = {
        row["name"] for row in component_rows if row["name"] in COMMON_COMPONENT_NAMES
    }
    assert common_names_in_project_zero == set(COMMON_COMPONENT_NAMES)


def test_build_catalog_common_components_are_shared_across_projects() -> None:
    """The 5 common component_version ids are IDENTICAL across every
    project's catalog (not re-created per project): this is what makes a
    search for a common name match every project instead of nothing.
    """
    from datetime import UTC, datetime

    from scripts.seed_load_test import COMMON_COMPONENT_NAMES, _build_catalog

    now = datetime.now(tz=UTC)
    _components, _versions, project_catalog = _build_catalog(
        project_count=3, components_per_scan=8, now=now
    )
    first_five = set(project_catalog[0][: len(COMMON_COMPONENT_NAMES)])
    for catalog in project_catalog[1:]:
        assert set(catalog[: len(COMMON_COMPONENT_NAMES)]) == first_five


def test_build_catalog_rare_component_only_in_first_project() -> None:
    """RARE_COMPONENT_NAME's component_version id appears in project 0's
    catalog and NO other project's: this is what makes a search for it
    return exactly one project instead of all of them (the "uncommon
    package name" query fixture, ``QUERY_UNCOMMON``).
    """
    from datetime import UTC, datetime

    from scripts.seed_load_test import RARE_COMPONENT_NAME, _build_catalog

    now = datetime.now(tz=UTC)
    component_rows, version_rows, project_catalog = _build_catalog(
        project_count=3, components_per_scan=8, now=now
    )
    rare_component_id = next(
        row["id"] for row in component_rows if row["name"] == RARE_COMPONENT_NAME
    )
    rare_version_id = next(
        row["id"] for row in version_rows if row["component_id"] == rare_component_id
    )
    assert rare_version_id in project_catalog[0]
    assert all(rare_version_id not in catalog for catalog in project_catalog[1:])
