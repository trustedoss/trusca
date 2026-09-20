"""Stage timings and degraded-stage records (U3-B, NF-1): the pure rules.

The write paths that call these are exercised against a real database in
``tests/integration/scan/test_stage_records.py``. This file pins the rules and
the places the same vocabulary is spelled out again.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest

from schemas.project_detail import DegradedStage
from services import scan_outcome as so

T0 = datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_advancing_closes_the_open_stage_and_opens_the_next() -> None:
    meta = so.advance_stage_timings({}, stage="fetch", now=_at(0))
    meta = so.advance_stage_timings(meta, stage="cdxgen", now=_at(5))
    meta = so.close_stage_timings(meta, now=_at(65))

    timings = meta[so.STAGE_TIMINGS_KEY]
    assert timings["fetch"] == {
        "started_at": "2026-09-21T12:00:00Z",
        "ended_at": "2026-09-21T12:00:05Z",
    }
    assert timings["cdxgen"] == {
        "started_at": "2026-09-21T12:00:05Z",
        "ended_at": "2026-09-21T12:01:05Z",
    }


def test_an_untimed_transition_leaves_no_entry_and_still_closes_the_previous_stage() -> None:
    """The reuse path announces prep and cdxgen without running either."""
    meta = so.advance_stage_timings({}, stage="fetch", now=_at(0))
    meta = so.advance_stage_timings(meta, stage="prep", now=_at(2), timed=False)
    meta = so.advance_stage_timings(meta, stage="cdxgen", now=_at(2), timed=False)
    meta = so.advance_stage_timings(meta, stage="sign", now=_at(3))

    timings: dict[str, Any] = meta[so.STAGE_TIMINGS_KEY]
    assert set(timings) == {"fetch", "sign"}
    assert timings["fetch"]["ended_at"] == "2026-09-21T12:00:02Z"


def test_entering_a_stage_twice_keeps_the_first_start() -> None:
    meta = so.advance_stage_timings({}, stage="trivy", now=_at(0))
    meta = so.advance_stage_timings(meta, stage="trivy", now=_at(9))
    assert meta[so.STAGE_TIMINGS_KEY]["trivy"]["started_at"] == "2026-09-21T12:00:00Z"


def test_other_metadata_survives_and_the_input_is_not_mutated() -> None:
    original: dict[str, Any] = {
        "detected_env": "node",
        so.STAGE_TIMINGS_KEY: {"fetch": {"started_at": "x"}},
    }
    merged = so.advance_stage_timings(original, stage="prep", now=_at(1))
    assert merged["detected_env"] == "node"
    assert original[so.STAGE_TIMINGS_KEY] == {"fetch": {"started_at": "x"}}


def test_reset_drops_both_records_and_nothing_else() -> None:
    meta = {
        "detected_env": "node",
        so.STAGE_TIMINGS_KEY: {"fetch": {}},
        so.STAGE_OUTCOMES_KEY: {"sign": {"reason": "failed"}},
    }
    assert so.reset_stage_records(meta) == {"detected_env": "node"}


def test_a_stage_outcome_is_recorded_and_listed() -> None:
    meta = so.add_stage_outcome({}, stage="sign", reason="failed")
    meta = so.add_stage_outcome(meta, stage="prep", reason="timeout")
    # Listed in the tuple's order, not insertion order, so the screen is stable.
    assert so.degraded_stages(meta) == [
        {"stage": "prep", "reason": "timeout"},
        {"stage": "sign", "reason": "failed"},
    ]


@pytest.mark.parametrize(
    ("stage", "reason"),
    [("nonsense", "failed"), ("sign", "nonsense"), ("", "failed")],
)
def test_a_value_outside_the_vocabulary_is_refused(stage: str, reason: str) -> None:
    with pytest.raises(ValueError):
        so.add_stage_outcome({}, stage=stage, reason=reason)


def test_no_record_reads_as_nothing_degraded() -> None:
    assert so.degraded_stages(None) == []
    assert so.degraded_stages({}) == []
    assert so.degraded_stages({so.STAGE_OUTCOMES_KEY: "garbage"}) == []
    assert so.degraded_stages({so.STAGE_OUTCOMES_KEY: {"sign": {"reason": "weird"}}}) == []


# ---------------------------------------------------------------------------
# The same vocabulary appears in three more places. Each is compared to the
# source of truth here, in both directions, so adding a stage in one place and
# forgetting another fails this file instead of shipping a blank label.
# ---------------------------------------------------------------------------

LOCALES = Path(__file__).resolve().parents[4] / "frontend" / "src" / "locales"


def _scan_gaps(lang: str) -> dict[str, str]:
    data = json.loads((LOCALES / lang / "project_detail.json").read_text(encoding="utf-8"))

    def find(node: object) -> dict[str, str] | None:
        if isinstance(node, dict):
            if "scan_gaps" in node:
                found_here: dict[str, str] = node["scan_gaps"]
                return found_here
            for value in node.values():
                found = find(value)
                if found is not None:
                    return found
        return None

    found = find(data)
    assert found is not None
    return found


def test_the_api_schema_lists_exactly_the_recorded_stages_and_reasons() -> None:
    fields = DegradedStage.model_fields
    assert set(get_args(fields["stage"].annotation)) == set(so.DEGRADABLE_STAGES)
    assert set(get_args(fields["reason"].annotation)) == set(so.STAGE_REASONS)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_every_listed_stage_and_reason_has_a_label_in_both_languages(lang: str) -> None:
    gaps = _scan_gaps(lang)
    # Scancode has its own notice and its own keys, and is not repeated in the list.
    listed = {s for s in so.DEGRADABLE_STAGES if s != "scancode"}
    assert {k[len("stage_") :] for k in gaps if k.startswith("stage_")} == listed
    assert {k[len("reason_") :] for k in gaps if k.startswith("reason_")} == set(so.STAGE_REASONS)


def test_the_frontend_type_lists_exactly_the_recorded_stages() -> None:
    source = (
        Path(__file__).resolve().parents[4]
        / "frontend"
        / "src"
        / "features"
        / "projects"
        / "api"
        / "projectDetailApi.ts"
    ).read_text(encoding="utf-8")
    block = source.split("degraded_stages: {", 1)[1].split("}[];", 1)[0]
    stage_part, reason_part = block.split("reason:", 1)
    import re

    assert set(re.findall(r'"([a-z_]+)"', stage_part)) == set(so.DEGRADABLE_STAGES)
    assert set(re.findall(r'"([a-z_]+)"', reason_part)) == set(so.STAGE_REASONS)
