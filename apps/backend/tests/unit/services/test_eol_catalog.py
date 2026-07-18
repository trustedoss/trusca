"""Unit tests — EOL evaluation logic (Phase M, services/eol/eol_catalog.py).

Fixtures are the BomLens originals (``tests/fixtures/eol/eol-data.json`` /
``eol-components.json``, vendored verbatim): the dataset's dates are far
past / far future so verdicts never depend on the run date, and the
component set exercises the exact edges the BomLens e2e verified —
including ``express-session``, which must NOT match the ``pkg:npm/express@``
prefix (the ``@`` terminator prevents over-matching).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from services.eol.eol_catalog import (
    EolDataset,
    EolRule,
    derive_cycle,
    evaluate,
    load_dataset,
    load_rules,
    stamp_component_version,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "eol"
TODAY = date(2026, 7, 11)


def _dataset() -> EolDataset:
    raw = json.loads((FIXTURES / "eol-data.json").read_text(encoding="utf-8"))
    return EolDataset(
        snapshot=raw["_snapshot"],
        products={k: v for k, v in raw.items() if not k.startswith("_")},
    )


def _rules() -> tuple[EolRule, ...]:
    return load_rules()


# ---------------------------------------------------------------------------
# derive_cycle — table (BomLens enrich-eol.sh:77-85 semantics)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "granularity", "expected"),
    [
        ("3.2.0", "major.minor", "3.2"),
        ("4.18.2", "major", "4"),
        ("v1.2.3", "major.minor", "1.2"),  # leading v stripped
        ("1.2.3-rc1", "major.minor", "1.2"),
        ("1.2.3-rc1", "major", "1"),
        ("5", "major.minor", "5"),  # single segment → major fallback
        ("5", "major", "5"),
        ("2rc1.4", "major.minor", "2.4"),  # leading-numeric part of each segment
        ("1.x.3", "major.minor", "1"),  # stops at first non-numeric-lead segment
        ("rc1", "major", None),  # fully non-numeric lead → unknown
        ("", "major", None),
        ("0.0.0", "major.minor", "0.0"),  # persist default version
    ],
)
def test_derive_cycle_table(
    version: str, granularity: str, expected: str | None
) -> None:
    assert derive_cycle(version, granularity) == expected


# ---------------------------------------------------------------------------
# evaluate — decision matrix
# ---------------------------------------------------------------------------


def test_dated_eol_in_the_past_is_eol() -> None:
    verdict = evaluate(
        "pkg:maven/org.springframework.boot/spring-boot-starter-web@3.2.0?type=jar",
        "3.2.0",
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.state == "eol"
    assert verdict.product == "spring-boot"
    assert verdict.cycle == "3.2"
    assert verdict.date == date(2020, 1, 1)
    assert verdict.source == "endoflife.date@2026-01-01"


def test_dated_eol_in_the_future_is_supported() -> None:
    verdict = evaluate(
        "pkg:maven/org.springframework.boot/spring-boot-actuator@3.3.1",
        "3.3.1",
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.state == "supported"
    assert verdict.date == date(2099, 12, 31)


def test_boolean_eol_values() -> None:
    eol_true = evaluate(
        "pkg:npm/express@3.1.0", "3.1.0",
        rules=_rules(), dataset=_dataset(), today=TODAY,
    )
    assert eol_true is not None and eol_true.state == "eol"
    assert eol_true.date is None  # boolean feeds carry no date

    eol_false = evaluate(
        "pkg:npm/express@4.18.2", "4.18.2",
        rules=_rules(), dataset=_dataset(), today=TODAY,
    )
    assert eol_false is not None and eol_false.state == "supported"


def test_unlisted_cycle_is_unknown() -> None:
    verdict = evaluate(
        "pkg:maven/org.springframework.boot/spring-boot-experimental@9.9.0",
        "9.9.0",
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.state == "unknown"
    assert verdict.cycle == "9.9"


def test_underivable_version_is_unknown_with_null_cycle() -> None:
    verdict = evaluate(
        "pkg:npm/express@latest", "latest",
        rules=_rules(), dataset=_dataset(), today=TODAY,
    )
    assert verdict is not None
    assert verdict.state == "unknown"
    assert verdict.cycle is None


def test_malformed_date_string_is_unknown() -> None:
    dataset = EolDataset(
        snapshot="2026-01-01",
        products={"express": [{"cycle": "4", "eol": "not-a-date"}]},
    )
    verdict = evaluate(
        "pkg:npm/express@4.1.0", "4.1.0",
        rules=_rules(), dataset=dataset, today=TODAY,
    )
    assert verdict is not None and verdict.state == "unknown"


def test_numeric_cycle_in_feed_matches_via_str() -> None:
    # endoflife.date sometimes emits numeric cycles (4 not "4").
    dataset = EolDataset(
        snapshot="2026-01-01", products={"express": [{"cycle": 4, "eol": False}]}
    )
    verdict = evaluate(
        "pkg:npm/express@4.1.0", "4.1.0",
        rules=_rules(), dataset=dataset, today=TODAY,
    )
    assert verdict is not None and verdict.state == "supported"


# ---------------------------------------------------------------------------
# Matching — closed whitelist, no over-match, %40 normalisation
# ---------------------------------------------------------------------------


def test_unmapped_component_returns_none() -> None:
    for purl, version in (
        ("pkg:npm/lodash@4.17.21", "4.17.21"),
        ("pkg:pypi/requests@2.31.0", "2.31.0"),
    ):
        assert (
            evaluate(purl, version, rules=_rules(), dataset=_dataset(), today=TODAY)
            is None
        )


def test_express_session_does_not_match_the_express_rule() -> None:
    # The trailing @ in "pkg:npm/express@" is the over-match terminator.
    assert (
        evaluate(
            "pkg:npm/express-session@1.17.3",
            "1.17.3",
            rules=_rules(),
            dataset=_dataset(),
            today=TODAY,
        )
        is None
    )


def test_url_encoded_scope_normalised_before_match() -> None:
    # cdxgen emits pkg:npm/%40angular/core@17.0.0; the map says @angular.
    dataset = EolDataset(
        snapshot="2026-01-01", products={"angular": [{"cycle": "17", "eol": True}]}
    )
    verdict = evaluate(
        "pkg:npm/%40angular/core@17.0.0",
        "17.0.0",
        rules=_rules(),
        dataset=dataset,
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.product == "angular"
    assert verdict.state == "eol"


def test_purl_qualifiers_do_not_break_prefix_match() -> None:
    # "?type=jar" trails the version — prefix matching is unaffected.
    verdict = evaluate(
        "pkg:maven/org.springframework.boot/spring-boot-starter-web@3.2.0?type=jar",
        "3.2.0",
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None and verdict.state == "eol"


# ---------------------------------------------------------------------------
# load_dataset — override + degradation
# ---------------------------------------------------------------------------


def test_load_dataset_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "snap.json"
    override.write_text(
        json.dumps({"_snapshot": "2026-06-01", "express": [{"cycle": "4", "eol": False}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EOL_SNAPSHOT_PATH", str(override))
    dataset = load_dataset()
    assert dataset is not None
    assert dataset.snapshot == "2026-06-01"


@pytest.mark.parametrize(
    "content",
    [
        "{ not json",
        json.dumps(["wrong", "shape"]),
        json.dumps({"express": []}),  # missing _snapshot
        json.dumps({"_snapshot": "2026-01-01"}),  # no products
    ],
)
def test_load_dataset_corrupt_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    bad = tmp_path / "snap.json"
    bad.write_text(content, encoding="utf-8")
    monkeypatch.setenv("EOL_SNAPSHOT_PATH", str(bad))
    assert load_dataset() is None


def test_load_dataset_missing_override_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EOL_SNAPSHOT_PATH", str(tmp_path / "absent.json"))
    assert load_dataset() is None


def test_vendored_dataset_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOL_SNAPSHOT_PATH", raising=False)
    dataset = load_dataset()
    assert dataset is not None
    assert dataset.cycles("spring-boot")  # vendored snapshot carries the map's products


# ---------------------------------------------------------------------------
# stamp_component_version — changed-value guard
# ---------------------------------------------------------------------------


class _FakeComponentVersion:
    eol_state: str | None = None
    eol_product: str | None = None
    eol_cycle: str | None = None
    eol_date: date | None = None
    eol_source: str | None = None
    eol_evaluated_at: datetime | None = None
    currency_state: str | None = None
    currency_latest: str | None = None
    currency_latest_release_date: date | None = None
    currency_evaluated_at: datetime | None = None


def test_stamp_writes_then_second_stamp_is_a_noop() -> None:
    row = _FakeComponentVersion()
    verdict = evaluate(
        "pkg:npm/express@3.1.0", "3.1.0",
        rules=_rules(), dataset=_dataset(), today=TODAY,
    )
    now = datetime(2026, 7, 11, 12, 0, 0)
    assert stamp_component_version(row, verdict, now) is True  # type: ignore[arg-type]
    assert row.eol_state == "eol"
    assert row.eol_evaluated_at == now

    later = datetime(2026, 7, 12, 12, 0, 0)
    assert stamp_component_version(row, verdict, later) is False  # type: ignore[arg-type]
    assert row.eol_evaluated_at == now  # unchanged row not re-dirtied


def test_stamp_none_verdict_leaves_row_untouched() -> None:
    row = _FakeComponentVersion()
    assert (
        stamp_component_version(row, None, datetime(2026, 7, 11)) is False  # type: ignore[arg-type]
    )
    assert row.eol_state is None
    assert row.eol_evaluated_at is None


def test_pathological_numeric_version_is_underivable() -> None:
    # security-reviewer M2 — a 33+-digit "major" would overflow the
    # VARCHAR(32) eol_cycle column at flush time, OUTSIDE the per-component
    # best-effort guard. Over-long segments must read as underivable.
    assert derive_cycle("1" * 40, "major") is None
    assert derive_cycle("1" * 40 + ".2", "major.minor") is None
    verdict = evaluate(
        "pkg:npm/express@" + "1" * 40,
        "1" * 40,
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.state == "unknown"
    assert verdict.cycle is None


# ---------------------------------------------------------------------------
# Version currency (0040) — offline "behind latest patch in release line"
# ---------------------------------------------------------------------------


def test_currency_outdated_when_installed_below_cycle_latest() -> None:
    # django 4.2 cycle latest is 4.2.16 in the fixture; 4.2.1 is behind.
    verdict = evaluate(
        "pkg:pypi/django@4.2.1",
        "4.2.1",
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.currency_state == "outdated"
    assert verdict.currency_latest == "4.2.16"


def test_currency_current_when_installed_at_or_above_cycle_latest() -> None:
    verdict = evaluate(
        "pkg:pypi/django@4.2.16",
        "4.2.16",
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.currency_state == "current"
    assert verdict.currency_latest == "4.2.16"


def test_currency_unknown_when_cycle_entry_has_no_latest() -> None:
    # express cycle 3 in the fixture carries `eol: true` but no `latest`.
    verdict = evaluate(
        "pkg:npm/express@3.1.0",
        "3.1.0",
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.currency_state == "unknown"
    assert verdict.currency_latest is None


def test_currency_unknown_when_no_cycle_match() -> None:
    # An unlisted cycle → no entry → currency stays unknown/NULL.
    verdict = evaluate(
        "pkg:pypi/django@9.9.9",
        "9.9.9",
        rules=_rules(),
        dataset=_dataset(),
        today=TODAY,
    )
    assert verdict is not None
    assert verdict.currency_state == "unknown"
    assert verdict.currency_latest is None


def test_currency_unmapped_component_returns_none() -> None:
    # Not in the whitelist → no verdict at all (columns stay NULL).
    assert (
        evaluate(
            "pkg:npm/left-pad@1.0.0",
            "1.0.0",
            rules=_rules(),
            dataset=_dataset(),
            today=TODAY,
        )
        is None
    )


def test_stamp_writes_currency_columns_and_is_idempotent() -> None:
    row = _FakeComponentVersion()
    verdict = evaluate(
        "pkg:pypi/django@4.2.1", "4.2.1",
        rules=_rules(), dataset=_dataset(), today=TODAY,
    )
    now = datetime(2026, 7, 11, 12, 0, 0)
    assert stamp_component_version(row, verdict, now) is True  # type: ignore[arg-type]
    assert row.currency_state == "outdated"
    assert row.currency_latest == "4.2.16"
    assert row.currency_evaluated_at == now

    later = datetime(2026, 7, 12, 12, 0, 0)
    assert stamp_component_version(row, verdict, later) is False  # type: ignore[arg-type]
    assert row.currency_evaluated_at == now  # unchanged row not re-dirtied
