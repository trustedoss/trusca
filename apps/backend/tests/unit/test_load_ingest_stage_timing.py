"""The pure parts of ``tests/load/ingest_stage_timing.py``.

The script measures from the outside, so its numbers are only as good as three
small pieces: the sample it uploads, the per-step spans it derives from polled
samples, and the queue-wait arithmetic. Each is pinned here, because a wrong
span or a sample that silently changes the ecosystem mix would produce a
plausible table that measures something else.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

LOAD = Path(__file__).resolve().parents[4] / "tests" / "load"
sys.path.insert(0, str(LOAD))
import ingest_stage_timing as its  # noqa: E402


def _document(n: int) -> dict:
    """n components cycling npm, gem, pypi, in blocks so a prefix is one ecosystem."""
    ecosystems = ["npm", "gem", "pypi"]
    comps = []
    for i in range(n):
        eco = ecosystems[(i * 3) // n]
        comps.append(
            {
                "bom-ref": f"ref-{i}",
                "purl": f"pkg:{eco}/pkg{i}@1.0.0",
                "licenses": [{"license": {"id": "MIT"}}] if i % 5 == 0 else [],
            }
        )
    deps = [
        {"ref": f"ref-{i}", "dependsOn": [f"ref-{(i + 1) % n}", f"ref-{(i + 7) % n}"]}
        for i in range(n)
    ]
    return {
        "bomFormat": "CycloneDX",
        "serialNumber": "urn:uuid:00000000-0000-0000-0000-000000000000",
        "metadata": {"component": {"bom-ref": "root"}},
        "components": comps,
        "dependencies": [{"ref": "root", "dependsOn": ["ref-0"]}, *deps],
    }


def _ecosystem_share(components: list[dict]) -> dict[str, int]:
    return dict(Counter(c["purl"].split(":")[1].split("/")[0] for c in components))


class TestSampleDocument:
    def test_keeps_the_ecosystem_mix_where_a_prefix_would_not(self) -> None:
        doc = _document(900)
        assert set(_ecosystem_share(doc["components"][:100])) == {"npm"}
        sampled = its.sample_document(doc, 90)
        share = _ecosystem_share(sampled["components"])
        assert len(sampled["components"]) == 90
        assert share == {"npm": 30, "gem": 30, "pypi": 30}

    def test_no_dependency_edge_points_at_a_dropped_component(self) -> None:
        sampled = its.sample_document(_document(300), 40)
        kept = {c["bom-ref"] for c in sampled["components"]} | {"root"}
        assert sampled["dependencies"], "the graph should not be emptied"
        for entry in sampled["dependencies"]:
            assert entry["ref"] in kept
            assert set(entry["dependsOn"]) <= kept

    def test_each_run_is_a_new_document(self) -> None:
        doc = _document(30)
        first = its.sample_document(doc, 0)["serialNumber"]
        second = its.sample_document(doc, 0)["serialNumber"]
        assert first != second != doc["serialNumber"]

    @pytest.mark.parametrize("n", [0, -1, 30, 500])
    def test_zero_or_at_least_all_keeps_every_component(self, n: int) -> None:
        assert len(its.sample_document(_document(30), n)["components"]) == 30

    def test_the_input_is_not_modified(self) -> None:
        doc = _document(60)
        before = len(doc["components"])
        its.sample_document(doc, 10)
        assert len(doc["components"]) == before

    def test_count_unlicensed(self) -> None:
        # _document gives every fifth component a license
        assert its.count_unlicensed(_document(50)["components"]) == 40


class TestDeriveSteps:
    def test_a_span_runs_from_first_seen_to_the_next_step(self) -> None:
        obs = [(0.0, None), (0.25, "bootstrap"), (0.5, "bootstrap"), (1.0, "conformance"),
               (1.25, "components"), (9.0, "components"), (9.25, "trivy")]
        spans = its.derive_steps(obs, finished_at=12.0)
        assert [(s["step"], s["seconds"]) for s in spans] == [
            ("bootstrap", 0.75),
            ("conformance", 0.25),
            ("components", 8.0),
            ("trivy", 2.75),
        ]

    def test_a_queued_scan_has_no_span_until_a_step_appears(self) -> None:
        assert its.derive_steps([(0.0, None), (0.25, None)], finished_at=1.0) == []

    def test_a_step_seen_once_ends_at_the_finish(self) -> None:
        spans = its.derive_steps([(2.0, "finalize")], finished_at=2.5)
        assert spans == [{"step": "finalize", "start": 2.0, "seconds": 0.5}]

    def test_the_total_of_the_spans_covers_first_step_to_finish(self) -> None:
        obs = [(0.1, "a"), (1.1, "b"), (4.1, "c")]
        spans = its.derive_steps(obs, finished_at=10.1)
        assert sum(s["seconds"] for s in spans) == pytest.approx(10.0)


class TestSecondsBetween:
    def test_difference_of_server_timestamps(self) -> None:
        assert its.seconds_between("2026-09-19T10:00:00Z", "2026-09-19T10:00:07.5+00:00") == 7.5

    @pytest.mark.parametrize("missing", [None, ""])
    def test_a_missing_timestamp_is_none_not_zero(self, missing: str | None) -> None:
        # A scan that never started has no started_at; reporting 0 s would read
        # as an instant queue wait.
        assert its.seconds_between("2026-09-19T10:00:00Z", missing) is None
        assert its.seconds_between(missing, "2026-09-19T10:00:00Z") is None
