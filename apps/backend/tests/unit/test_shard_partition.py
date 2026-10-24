"""tests/_shard.py: how CI splits the integration suite across jobs.

The property that matters is that every collected file runs in exactly one
shard. A split that drops a file leaves the job green with tests unrun, so
these tests check the partition itself, the pytest options that apply it to
a real collection, and the check the aggregate CI job runs over the reports.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _shard

BACKEND_ROOT = Path(__file__).resolve().parents[2]

FILES = [f"tests/integration/test_{c}.py" for c in "abcdefghij"]


@pytest.mark.parametrize("total", [1, 2, 3, 4, 7, 10, 12])
def test_every_file_lands_in_exactly_one_bucket(total: int) -> None:
    durations = {f: float(i * 7 % 11 + 1) for i, f in enumerate(FILES)}
    buckets = _shard.partition(FILES, durations, total)

    assert len(buckets) == total
    flat = [f for b in buckets for f in b]
    assert sorted(flat) == sorted(FILES)
    assert len(flat) == len(set(flat))


def test_buckets_keep_collection_order() -> None:
    buckets = _shard.partition(FILES, {}, 3)
    for bucket in buckets:
        assert bucket == [f for f in FILES if f in bucket]


def test_assignment_does_not_depend_on_arrival_order() -> None:
    durations = {f: float(len(f) + i) for i, f in enumerate(FILES)}
    forward = _shard.partition(FILES, durations, 3)
    backward = _shard.partition(list(reversed(FILES)), durations, 3)
    assert [set(b) for b in forward] == [set(b) for b in backward]


def test_longest_files_are_spread_out() -> None:
    durations = {f: 1.0 for f in FILES}
    durations[FILES[0]] = 100.0
    durations[FILES[1]] = 90.0
    buckets = _shard.partition(FILES, durations, 2)
    assert FILES[0] in buckets[0]
    assert FILES[1] in buckets[1]
    # Everything else goes to the lighter bucket until it catches up.
    assert buckets[0] == [FILES[0]]


def test_unknown_files_get_the_median_weight() -> None:
    durations = {FILES[0]: 10.0, FILES[1]: 20.0, FILES[2]: 90.0}
    # FILES[3] is unknown and weighs the median, 20: {90}, {20, 10}, {20}.
    # Weighed as the mean (40) it would pair with 10 instead; weighed as 1 it
    # would go last and share a bucket with FILES[0].
    buckets = _shard.partition(FILES[:4], durations, 3)
    assert buckets == [[FILES[2]], [FILES[0], FILES[1]], [FILES[3]]]


def test_duplicate_file_entries_count_once() -> None:
    buckets = _shard.partition(FILES[:2] + FILES[:2], {}, 2)
    assert sorted(f for b in buckets for f in b) == sorted(FILES[:2])


@pytest.mark.parametrize(("value", "expected"), [("1/1", (1, 1)), ("2/3", (2, 3)), ("3/3", (3, 3))])
def test_parse_shard_accepts(value: str, expected: tuple[int, int]) -> None:
    assert _shard.parse_shard(value) == expected


@pytest.mark.parametrize("value", ["0/3", "4/3", "3", "a/b", "1/0", "-1/3", ""])
def test_parse_shard_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="K/N"):
        _shard.parse_shard(value)


def _report(shard: int, total: int, files: list[str], tests: int, **over: object) -> dict:
    base = {
        "shard": shard,
        "total": total,
        "files_total": 4,
        "tests_total": 10,
        "files": files,
        "tests_selected": tests,
        "durations": {f: 1.5 for f in files},
        "slowest_tests": [],
    }
    base.update(over)
    return base


def _good_reports() -> list[dict]:
    return [
        _report(1, 2, FILES[0:2], 6),
        _report(2, 2, FILES[2:4], 4),
    ]


def test_verify_accepts_a_complete_split() -> None:
    assert _shard.verify(_good_reports()) == []


def test_verify_rejects_no_reports() -> None:
    assert _shard.verify([]) == ["no shard reports were found"]


def test_verify_rejects_a_missing_shard() -> None:
    problems = _shard.verify(_good_reports()[:1])
    assert "expected reports from shards 1..2, got [1]" in problems
    assert "shards ran 2 distinct files, 4 were collected" in problems
    assert "shards ran 6 tests, 10 were collected" in problems


def test_verify_rejects_a_file_run_twice() -> None:
    reports = _good_reports()
    reports[1] = _report(2, 2, [FILES[1], FILES[3]], 4)
    problems = _shard.verify(reports)
    assert f"{FILES[1]} ran in shard 1 and shard 2" in problems
    assert "shards ran 3 distinct files, 4 were collected" in problems


def test_verify_rejects_a_test_count_gap() -> None:
    reports = _good_reports()
    reports[1]["tests_selected"] = 3
    assert _shard.verify(reports) == ["shards ran 9 tests, 10 were collected"]


def test_verify_rejects_shards_that_collected_differently() -> None:
    reports = _good_reports()
    reports[1]["tests_total"] = 11
    problems = _shard.verify(reports)
    assert len(problems) == 1
    assert problems[0].startswith("shards disagree on what was collected")


def test_verify_cli_exit_code_and_durations(tmp_path: Path) -> None:
    paths = []
    for r in _good_reports():
        p = tmp_path / f"r{r['shard']}.json"
        p.write_text(json.dumps(r), encoding="utf-8")
        paths.append(str(p))
    out = tmp_path / "durations.json"

    assert _shard.main(["verify", *paths, "--durations-out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {f: 1.5 for f in FILES[:4]}

    out.unlink()
    assert _shard.main(["verify", paths[0], "--durations-out", str(out)]) == 1
    assert not out.exists()


def test_committed_durations_file_parses() -> None:
    durations = _shard.load_durations()
    assert durations, "tests/integration/shard_durations.json is missing or empty"
    for path, seconds in durations.items():
        assert path.startswith("tests/integration/") and path.endswith(".py"), path
        assert seconds >= 0


def test_load_durations_without_a_file(tmp_path: Path) -> None:
    assert _shard.load_durations(tmp_path / "absent.json") == {}


# The options as CI uses them, on a real collection. --collect-only keeps it
# fast and keeps this file from running itself.
SAMPLE = [
    "tests/unit/test_shard_partition.py",
    "tests/unit/test_check_config.py",
    "tests/unit/test_closed_status_vocabulary.py",
]


def _collect(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *args,
            *SAMPLE,
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_shard_option_splits_a_real_collection(tmp_path: Path) -> None:
    reports = []
    for k in (1, 2):
        path = tmp_path / f"shard-{k}.json"
        result = _collect(tmp_path, "--shard", f"{k}/2", "--shard-report", str(path))
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(path.read_text(encoding="utf-8"))
        assert f"shard {k}/2: {len(report['files'])} of 3 files" in result.stdout
        reports.append(report)

    assert _shard.verify(reports) == []
    assert sorted(f for r in reports for f in r["files"]) == sorted(SAMPLE)
    assert all(r["files"] for r in reports)


def test_shard_option_rejects_a_bad_value(tmp_path: Path) -> None:
    result = _collect(tmp_path, "--shard", "3/2")
    assert result.returncode != 0
    assert "--shard expects K/N" in result.stderr


# The workflow wiring. Each of these is something that, if it drifted, would
# leave `test (backend-integration)` green with tests unrun or unjudged.
def _jobs() -> dict:
    import yaml

    workflow = BACKEND_ROOT.parent.parent / ".github" / "workflows" / "ci.yml"
    jobs: dict = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_shards_get_the_same_services_and_env_as_the_test_job() -> None:
    jobs = _jobs()
    assert jobs["test-integration-shards"]["services"] == jobs["test"]["services"]
    assert jobs["test-integration-shards"]["env"] == jobs["test"]["env"]


def test_shard_count_comes_from_the_matrix() -> None:
    shards = _jobs()["test-integration-shards"]
    assert shards["strategy"]["fail-fast"] is False
    matrix = shards["strategy"]["matrix"]["shard"]
    assert matrix == list(range(1, len(matrix) + 1))
    run = next(s for s in shards["steps"] if "pytest" in str(s.get("run", "")))
    assert run["env"]["SHARDS"] == "${{ strategy.job-total }}"
    assert '--shard "$SHARD/$SHARDS"' in run["run"]
    assert run["run"].rstrip().splitlines()[-3].strip() == "tests/integration"


def test_the_required_check_aggregates_every_shard() -> None:
    jobs = _jobs()
    aggregate = jobs["test-integration"]
    assert aggregate["name"] == "test (backend-integration)"
    assert aggregate["needs"] == "test-integration-shards"
    # Without always() a failed shard skips this job, and a skipped required
    # check counts as passing.
    assert aggregate["if"] == "always()"
    first = aggregate["steps"][0]
    assert first["env"]["RESULT"] == "${{ needs.test-integration-shards.result }}"
    assert '[ "$RESULT" != "success" ]' in first["run"]
    assert any("tests._shard verify" in str(s.get("run", "")) for s in aggregate["steps"])

    gate = jobs["coverage-gate"]
    assert set(gate["needs"]) == {"test", "test-integration"}
    assert "test-integration-shards" in jobs["nightly-alert"]["needs"]
    # No other job may carry the required check's name.
    names = [j.get("name") for k, j in jobs.items() if k != "test-integration"]
    assert "test (backend-integration)" not in names
    assert "backend-integration" not in jobs["test"]["strategy"]["matrix"]["target"]
