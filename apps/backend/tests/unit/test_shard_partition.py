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
    durations = _shard.load_durations(_shard.DURATIONS_PATH)
    assert durations, "tests/integration/shard_durations.json is missing or empty"
    for path, seconds in durations.items():
        assert path.startswith("tests/integration/") and path.endswith(".py"), path
        assert seconds >= 0


def test_unit_durations_file_if_committed_parses() -> None:
    # Optional, so absent is fine; present, it must hold unit files only, or a
    # misplaced integration file would silently weigh the wrong suite.
    for path, seconds in _shard.load_durations(_shard.UNIT_DURATIONS_PATH).items():
        assert path.startswith("tests/unit/") and path.endswith(".py"), path
        assert seconds >= 0


def test_default_load_merges_every_durations_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text(json.dumps({"tests/integration/test_a.py": 2}), encoding="utf-8")
    second.write_text(json.dumps({"tests/unit/test_b.py": 3}), encoding="utf-8")
    monkeypatch.setattr(_shard, "DURATIONS_PATHS", (first, tmp_path / "absent.json", second))
    assert _shard.load_durations() == {
        "tests/integration/test_a.py": 2.0,
        "tests/unit/test_b.py": 3.0,
    }


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


#: (shard job, aggregate job, required check name, test directory, artifact stem)
SUITES = [
    ("test-unit-shards", "test-unit", "test (backend-unit)", "tests/unit", "backend-unit"),
    (
        "test-integration-shards",
        "test-integration",
        "test (backend-integration)",
        "tests/integration",
        "backend-integration",
    ),
]


def test_shards_get_the_same_services_and_env() -> None:
    jobs = _jobs()
    assert jobs["test-integration-shards"]["services"] == jobs["test-unit-shards"]["services"]
    assert jobs["test-integration-shards"]["env"] == jobs["test-unit-shards"]["env"]


@pytest.mark.parametrize(("shard_job", "directory"), [(s[0], s[3]) for s in SUITES])
def test_shard_count_comes_from_the_matrix(shard_job: str, directory: str) -> None:
    shards = _jobs()[shard_job]
    assert shards["strategy"]["fail-fast"] is False
    matrix = shards["strategy"]["matrix"]["shard"]
    assert matrix == list(range(1, len(matrix) + 1))
    run = next(s for s in shards["steps"] if "pytest" in str(s.get("run", "")))
    assert run["env"]["SHARDS"] == "${{ strategy.job-total }}"
    assert '--shard "$SHARD/$SHARDS"' in run["run"]
    assert run["run"].rstrip().splitlines()[-3].strip() == directory


@pytest.mark.parametrize(("shard_job", "aggregate_job", "check", "stem"), [
    (s[0], s[1], s[2], s[4]) for s in SUITES
])
def test_the_required_check_aggregates_every_shard(
    shard_job: str, aggregate_job: str, check: str, stem: str
) -> None:
    jobs = _jobs()
    aggregate = jobs[aggregate_job]
    assert aggregate["name"] == check
    assert set(aggregate["needs"]) == {"changes", shard_job}
    # Without always() a failed shard skips this job, and a skipped required
    # check counts as passing.
    assert aggregate["if"] == "always()"
    first = aggregate["steps"][0]
    assert first["env"]["RESULT"] == f"${{{{ needs.{shard_job}.result }}}}"
    assert '[ "$RESULT" != "success" ]' in first["run"]
    assert any("tests._shard verify" in str(s.get("run", "")) for s in aggregate["steps"])
    download = next(s for s in aggregate["steps"] if "download-artifact" in str(s.get("uses")))
    assert download["with"]["pattern"] == f"{stem}-shard-report-*"

    gate = jobs["coverage-gate"]
    assert set(gate["needs"]) == {"changes", "test-unit", "test-integration"}
    assert shard_job in jobs["nightly-alert"]["needs"]
    # A failed `verify` leaves every shard green, so the alert must also see
    # the aggregator.
    assert aggregate_job in jobs["nightly-alert"]["needs"]
    # No other job may carry the required check's name.
    names = [j.get("name") for k, j in jobs.items() if k != aggregate_job]
    assert check not in names


def test_the_frontend_test_job_keeps_its_required_name() -> None:
    jobs = _jobs()
    frontend = [k for k, j in jobs.items() if j.get("name") == "test (frontend)"]
    assert frontend == ["test-frontend"]
    assert "strategy" not in jobs["test-frontend"]


# The frontend-only skip (the `changes` job in ci.yml). A required check that
# is skipped counts as passing, so each of these is a way for a pull request
# to merge with a backend check that never ran.

#: Jobs that a frontend-only pull request may skip outright.
SKIPPABLE = {
    "test-integration-shards",
    "postgres-init-l1",
    "image-scan",
}
#: Matrix jobs whose backend leg must start (so it reports) and skip its steps.
STEP_GATED = {"lint", "typecheck"}


def test_changes_answers_true_unless_a_pull_request_is_frontend_only() -> None:
    changes = _jobs()["changes"]
    assert changes["outputs"]["backend"] == "${{ steps.classify.outputs.backend }}"
    run = next(s for s in changes["steps"] if s.get("id") == "classify")["run"]
    # The default, and the only place it is turned off.
    assert run.count("backend=true") == 1 and run.count("backend=false") == 1
    assert '[ "$EVENT" = "pull_request" ]' in run
    assert "--no-renames" in run
    assert "^apps/frontend/" in run
    assert "docs-site" not in run


def test_the_changes_job_classifies_a_real_merge(tmp_path: Path) -> None:
    """Run the workflow's own script against a merge commit, both ways."""
    script = next(s for s in _jobs()["changes"]["steps"] if s.get("id") == "classify")["run"]

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603 - fixed argv
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],  # noqa: S607
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    def classify(event: str, *changed: str) -> str:
        git("checkout", "-q", "-B", "base", "root")
        git("checkout", "-q", "-B", "topic", "root")
        for name in changed:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(name, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "change")
        git("checkout", "-q", "base")
        git("merge", "-q", "--no-ff", "-m", "merge", "topic")
        output = tmp_path.parent / f"{tmp_path.name}-output"
        output.write_text("", encoding="utf-8")
        subprocess.run(  # noqa: S603 - fixed argv
            ["bash", "-c", script],  # noqa: S607
            cwd=tmp_path,
            env={"EVENT": event, "GITHUB_OUTPUT": str(output), "PATH": "/usr/bin:/bin"},
            check=True,
            capture_output=True,
        )
        return output.read_text(encoding="utf-8").strip()

    git("init", "-q")
    (tmp_path / "README").write_text("root", encoding="utf-8")
    (tmp_path / "apps/backend").mkdir(parents=True)
    (tmp_path / "apps/backend/moved.py").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "root")
    git("branch", "root")

    assert classify("pull_request", "apps/frontend/src/a.ts") == "backend=false"
    assert classify("pull_request", "apps/frontend/a.ts", "apps/backend/b.py") == "backend=true"
    assert classify("pull_request", "docs-site/docs/a.md") == "backend=true"
    assert classify("pull_request", "apps/frontend-other/a.ts") == "backend=true"
    assert classify("push", "apps/frontend/src/a.ts") == "backend=true"
    assert classify("schedule", "apps/frontend/src/a.ts") == "backend=true"
    assert classify("workflow_dispatch", "apps/frontend/src/a.ts") == "backend=true"

    # A rename out of apps/backend lists the deleted side too.
    git("checkout", "-q", "-B", "topic", "root")
    (tmp_path / "apps/frontend").mkdir(parents=True, exist_ok=True)
    git("mv", "apps/backend/moved.py", "apps/frontend/moved.py")
    git("commit", "-q", "-m", "move")
    git("checkout", "-q", "-B", "base", "root")
    git("merge", "-q", "--no-ff", "-m", "merge", "topic")
    output = tmp_path.parent / f"{tmp_path.name}-output"
    output.write_text("", encoding="utf-8")
    env = {"EVENT": "pull_request", "GITHUB_OUTPUT": str(output), "PATH": "/usr/bin:/bin"}
    subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, check=True)  # noqa: S603,S607
    assert output.read_text(encoding="utf-8").strip() == "backend=true"

    # Not a merge commit: its parent is not the base, so the diff against it
    # says nothing about the pull request. A frontend-only commit on top of
    # the root makes that diff look frontend-only, so this fails unless the
    # merge check itself is what answers true.
    git("checkout", "-q", "-B", "linear", "root")
    (tmp_path / "apps/frontend").mkdir(parents=True, exist_ok=True)
    (tmp_path / "apps/frontend/linear.ts").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "linear")
    output.write_text("", encoding="utf-8")
    subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, check=True)  # noqa: S603,S607
    assert output.read_text(encoding="utf-8").strip() == "backend=true"


@pytest.mark.parametrize("job", sorted(SKIPPABLE))
def test_skippable_jobs_skip_only_on_an_explicit_false(job: str) -> None:
    spec = _jobs()[job]
    needs = spec["needs"] if isinstance(spec["needs"], list) else [spec["needs"]]
    assert "changes" in needs
    # `!cancelled()` makes a failed `changes` job run this rather than skip it,
    # and `!= 'false'` makes an empty answer run it too.
    assert spec["if"] == "${{ !cancelled() && needs.changes.outputs.backend != 'false' }}"


def test_coverage_gate_skips_only_on_an_explicit_false() -> None:
    gate = _jobs()["coverage-gate"]
    # No status function: success() is implied, so the gate still waits for
    # every backend test job to pass.
    assert gate["if"] == "needs.changes.outputs.backend != 'false'"


@pytest.mark.parametrize("job", sorted(STEP_GATED))
def test_matrix_backend_legs_always_start(job: str) -> None:
    spec = _jobs()[job]
    assert spec["strategy"]["matrix"]["target"] == ["backend", "frontend"]
    assert spec["if"] == "${{ !cancelled() }}"
    assert spec["env"]["BACKEND_LEG"] == (
        "${{ matrix.target == 'backend' && needs.changes.outputs.backend != 'false' }}"
    )
    for step in spec["steps"]:
        condition = str(step.get("if", ""))
        # Every backend step goes through BACKEND_LEG; a step still keyed on
        # the bare matrix target would run on a frontend-only pull request,
        # and one keyed on nothing would run on the frontend leg too.
        assert condition != "matrix.target == 'backend'", step.get("name")


@pytest.mark.parametrize("job", ["test-unit-shards", "test-frontend", "test-unit"])
def test_jobs_that_read_apps_frontend_are_never_skipped(job: str) -> None:
    spec = _jobs()[job]
    assert "needs.changes.outputs.backend" not in str(spec.get("if", ""))


def test_the_integration_aggregate_accepts_a_skip_only_when_told_to() -> None:
    first = _jobs()["test-integration"]["steps"][0]
    assert first["env"]["BACKEND"] == "${{ needs.changes.outputs.backend }}"
    assert '[ "$BACKEND" = "false" ] && [ "$RESULT" = "skipped" ]' in first["run"]

    def run(result: str, backend: str) -> int:
        return subprocess.run(  # noqa: S603 - fixed argv
            ["bash", "-c", first["run"]],  # noqa: S607
            env={
                "RESULT": result,
                "BACKEND": backend,
                "GITHUB_OUTPUT": "/dev/null",
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
            check=False,
        ).returncode

    assert run("skipped", "false") == 0
    assert run("success", "true") == 0
    assert run("skipped", "true") != 0
    assert run("skipped", "") != 0
    assert run("failure", "false") != 0
    assert run("cancelled", "false") != 0


def test_the_unit_aggregate_is_red_when_changes_did_not_succeed() -> None:
    first = _jobs()["test-unit"]["steps"][0]
    assert first["env"]["CHANGES"] == "${{ needs.changes.result }}"

    def run(result: str, changes: str) -> int:
        return subprocess.run(  # noqa: S603 - fixed argv
            ["bash", "-c", first["run"]],  # noqa: S607
            env={"RESULT": result, "CHANGES": changes, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            check=False,
        ).returncode

    assert run("success", "success") == 0
    assert run("success", "failure") != 0
    assert run("success", "skipped") != 0
    assert run("failure", "success") != 0
