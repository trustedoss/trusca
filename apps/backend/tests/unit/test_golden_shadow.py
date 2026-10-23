"""The golden shadow run: workflow wiring, engine overlay and difference table.

A shadow run (``golden-nightly.yml`` dispatched with ``shadow=true``) scans the
golden fixtures with the in-process engine and then with the BomLens engine on
one stack, and ``shadow_report.py`` turns the two capture folders into a
difference table. It only runs in CI on dispatch, so what can drift silently
is checked here: the overlay's security settings against the production
compose file, the workflow gating that keeps the nightly unchanged, and the
report against captures in the harness's own format.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from services import scan_outcome

REPO = Path(__file__).resolve().parents[4]
GOLDEN = REPO / "apps/backend/tests/e2e/golden"
WORKFLOW = REPO / ".github/workflows/golden-nightly.yml"
OVERLAY = GOLDEN / "compose.bomlens.yml"

sys.path.insert(0, str(GOLDEN))
import run_golden as rg  # noqa: E402
import shadow_report as sr  # noqa: E402


def _load(path: Path) -> dict:
    data: dict = yaml.safe_load(path.read_text())
    return data


def _workflow() -> dict:
    return _load(WORKFLOW)


def _steps() -> list[dict]:
    steps: list[dict] = _workflow()["jobs"]["golden"]["steps"]
    return steps


def _step(prefix: str) -> dict:
    return next(s for s in _steps() if s.get("name", "").startswith(prefix))


def _overlay() -> dict:
    return _load(OVERLAY)


def _prod() -> dict:
    return _load(REPO / "docker-compose.yml")


# ---------------------------------------------------------------------------
# Workflow wiring
# ---------------------------------------------------------------------------


class TestWorkflow:
    def test_shadow_is_an_opt_in_dispatch_input(self) -> None:
        wf = _workflow()
        triggers = wf.get("on", wf.get(True))
        shadow = triggers["workflow_dispatch"]["inputs"]["shadow"]
        assert shadow["type"] == "boolean"
        assert shadow["default"] is False
        # A schedule has no inputs: SHADOW falls back to 'false' there.
        expected = "${{ github.event.inputs.shadow || 'false' }}"
        assert wf["jobs"]["golden"]["env"]["SHADOW"] == expected

    def test_the_gate_and_the_baseline_rewrite_stand_down_in_a_shadow_run(self) -> None:
        assert _step("Golden drift gate")["if"] == (
            "env.UPDATE_BASELINES != 'true' && env.SHADOW != 'true'"
        )
        for name in ("Regenerate baselines", "Upload regenerated baselines"):
            assert _step(name)["if"] == "env.UPDATE_BASELINES == 'true' && env.SHADOW != 'true'"

    def test_every_shadow_step_runs_only_in_a_shadow_run(self) -> None:
        shadow_steps = [
            s for s in _steps()
            if s.get("name", "").startswith(("Shadow", "Upload shadow"))
        ]
        assert len(shadow_steps) >= 8, [s["name"] for s in shadow_steps]
        for step in shadow_steps:
            assert "env.SHADOW == 'true'" in step["if"], step["name"]

    def test_both_passes_record_and_neither_diffs(self) -> None:
        for prefix, folder in (("Shadow pass 1", "inprocess"), ("Shadow pass 2", "bomlens")):
            run = _step(prefix)["run"]
            assert f"--shadow-out golden-shadow/{folder}" in run
            assert "--update" not in run

    def test_the_switch_layers_the_overlay_on_the_nightly_stack(self) -> None:
        job_files = _workflow()["jobs"]["golden"]["env"]["COMPOSE_FILE"]
        switch = _step("Shadow: switch the stack")
        assert switch["env"]["COMPOSE_FILE"] == (
            job_files + ":apps/backend/tests/e2e/golden/compose.bomlens.yml"
        )
        assert switch["env"]["SCAN_ENGINE"] == "bomlens"
        assert switch["env"]["COMPOSE_PROFILES"] == "bomlens-engine"
        run = switch["run"]
        # The egress network is made with inter-container traffic off and the
        # result is checked, not assumed.
        assert "--opt com.docker.network.bridge.enable_icc=false trustedoss-scan-egress" in run
        assert '[ "$icc" != "false" ]' in run
        # The backend is recreated (it reads SCAN_ENGINE at dispatch) and the
        # engine it dispatches with is checked afterwards. It is removed first:
        # docker-compose 1.29 fails to recreate on current Docker Engine.
        lines = [line.strip() for line in run.splitlines()]
        remove = lines.index("docker-compose rm -fs backend")
        assert lines[remove + 1] == "docker-compose up -d --no-deps backend"
        assert "docker-compose up -d --no-deps docker-socket-proxy worker-engine" in lines
        assert "printenv SCAN_ENGINE" in run
        # Trivy volume comes from the in-process worker's own mount.
        assert '.Destination "/var/lib/trivy"' in run

    def test_no_engine_image_is_overridden(self) -> None:
        text = WORKFLOW.read_text() + OVERLAY.read_text()
        assert not re.search(r"^\s*SCAN_BOMLENS_(DEEP_LICENSE_)?IMAGE\s*:", text, re.M)

    def test_the_report_job_runs_only_for_a_shadow_dispatch(self) -> None:
        job = _workflow()["jobs"]["shadow-report"]
        assert job["needs"] == ["golden", "golden-containers"]
        assert "github.event_name == 'workflow_dispatch'" in job["if"]
        assert "github.event.inputs.shadow == 'true'" in job["if"]
        run = next(s["run"] for s in job["steps"] if s.get("name") == "Build the difference table")
        assert "shadow_report.py" in run
        assert '>> "$GITHUB_STEP_SUMMARY"' in run

    def test_the_nightly_alert_still_follows_the_schedule_only(self) -> None:
        alert = _workflow()["jobs"]["nightly-alert"]
        assert alert["if"] == "${{ always() && github.event_name == 'schedule' }}"
        assert alert["needs"] == "golden"


# ---------------------------------------------------------------------------
# Overlay against the production engine services
# ---------------------------------------------------------------------------


class TestOverlay:
    def test_it_adds_the_two_engine_services_and_touches_nothing_else(self) -> None:
        assert set(_overlay()["services"]) == {"worker-engine", "docker-socket-proxy"}

    def test_the_socket_proxy_matches_production(self) -> None:
        mine = _overlay()["services"]["docker-socket-proxy"]
        prod = _prod()["services"]["docker-socket-proxy"]
        for key in ("image", "environment", "cap_drop", "security_opt", "volumes", "profiles"):
            assert mine[key] == prod[key], key
        assert mine["networks"] == ["docker-api"]

    def test_only_the_proxy_mounts_the_docker_socket(self) -> None:
        for name, svc in _overlay()["services"].items():
            mounts = " ".join(str(v) for v in svc.get("volumes", []))
            assert ("docker.sock" in mounts) == (name == "docker-socket-proxy"), name

    def test_the_engine_worker_matches_production_where_it_matters(self) -> None:
        mine = _overlay()["services"]["worker-engine"]
        prod = _prod()["services"]["worker-engine"]
        env = mine["environment"]
        assert env["DOCKER_HOST"] == prod["environment"]["DOCKER_HOST"]
        remote = "CELERY_WORKER_REMOTE_CONTROL"
        assert env[remote] == prod["environment"][remote]
        assert env["CELERY_WORKER_REMOTE_CONTROL"] == "false"
        assert mine["healthcheck"] == prod["healthcheck"]
        assert mine["profiles"] == prod["profiles"]
        assert mine["stop_grace_period"] == prod["stop_grace_period"]
        # Consumes the engine queue and nothing else, as production's default.
        assert mine["command"].split("-Q ")[1] == "trustedoss.engine"
        assert prod["command"].endswith("-Q ${WORKER_ENGINE_QUEUES:-trustedoss.engine}")
        assert "docker-api" in mine["networks"]

    def test_networks_follow_production(self) -> None:
        mine, prod = _overlay()["networks"], _prod()["networks"]
        assert mine["docker-api"]["internal"] is True
        assert prod["docker-api"]["internal"] is True
        egress = mine["bomlens-egress"]
        assert egress == {"name": prod["scan-egress"]["name"], "external": True}
        assert _overlay()["services"]["worker-engine"]["environment"]["SCAN_BOMLENS_NETWORK"] == (
            prod["scan-egress"]["name"]
        )

    def test_the_engine_worker_takes_the_in_process_workers_environment(self) -> None:
        mine = _overlay()["services"]["worker-engine"]
        assert mine["env_file"] == [
            "${GOLDEN_ENGINE_ENV_FILE:?set by golden-nightly.yml; see the header of this file}"
        ]
        switch = _step("Shadow: switch the stack")["run"]
        assert "{{range .Config.Env}}" in switch
        assert '> "$GOLDEN_ENGINE_ENV_FILE"' in switch


# ---------------------------------------------------------------------------
# Harness: shadow capture
# ---------------------------------------------------------------------------


SCAN_ROW = {
    "id": "s1",
    "status": "succeeded",
    "started_at": "2026-10-12T10:00:00Z",
    "completed_at": "2026-10-12T10:01:30.5Z",
    "error_message": None,
    "metadata": {
        "scan_engine": {"name": "bomlens", "run_status": "succeeded_degraded",
                        "image_digest": "sha256:abc"},
        "stage_outcomes": {"scancode": {"reason": "timeout"}, "generate": {"reason": "fallback"}},
    },
}

VULN_ROWS = [
    {"cve_id": "CVE-2024-1", "affected_component_name": "a", "affected_component_version": "1"},
    {"cve_id": "CVE-2024-1", "affected_component_name": "b", "affected_component_version": "2"},
    {"cve_id": "GHSA-xxxx", "affected_component_name": "a", "affected_component_version": "1"},
]


class _FakeApi:
    """Answers the harness's HTTP calls for one project and one scan."""

    def __init__(self, page_size: int = 2) -> None:
        self.page_size = page_size
        self.vuln_calls = 0

    def __call__(self, method, url, *, token=None, json_body=None, raw_body=None, ctype=None):
        path = url.split("http://api", 1)[1]
        if method == "POST" and path == "/v1/projects":
            return 201, json.dumps({"id": "p1"}).encode(), {}
        if method == "POST" and path.endswith("/source-archive"):
            return 201, json.dumps({"archive_id": "a1"}).encode(), {}
        if method == "POST" and path.endswith("/scans"):
            return 202, json.dumps({"id": "s1"}).encode(), {}
        if path == "/v1/scans/s1":
            return 200, json.dumps(SCAN_ROW).encode(), {}
        if path == "/v1/projects/p1/vulnerabilities":
            # The baseline capture's count, which every run reads.
            return 200, json.dumps({"items": [], "total": len(VULN_ROWS)}).encode(), {}
        if path.startswith("/v1/projects/p1/vulnerabilities?"):
            self.vuln_calls += 1
            offset = int(re.search(r"offset=(\d+)", path).group(1))
            items = VULN_ROWS[offset:offset + self.page_size]
            body = {"items": items, "total": len(VULN_ROWS), "offset": offset}
            return 200, json.dumps(body).encode(), {}
        if path.startswith("/v1/projects/p1/components"):
            return 200, json.dumps({"items": [{"purl": "pkg:npm/a@1"}], "total": 1}).encode(), {}
        return 404, b"{}", {}


class TestShadowCapture:
    def test_shadow_fields_from_a_scan_row(self) -> None:
        got = rg.shadow_fields(SCAN_ROW, VULN_ROWS, 91.24)
        assert got["vulnerability_ids"] == ["CVE-2024-1", "GHSA-xxxx"]
        assert got["vulnerability_findings"] == [
            "CVE-2024-1|a@1", "CVE-2024-1|b@2", "GHSA-xxxx|a@1",
        ]
        assert got["scan"]["engine"] == "bomlens"
        assert got["scan"]["stage_outcomes"] == {"generate": "fallback", "scancode": "timeout"}
        assert got["wall_seconds"] == 91.2

    def test_an_in_process_row_names_its_engine(self) -> None:
        got = rg.shadow_fields({"status": "succeeded", "metadata": {}}, [], 1.0)
        assert got["scan"]["engine"] == "inprocess"
        assert got["vulnerability_ids"] == []

    def test_an_unreadable_vulnerability_list_is_unknown_not_empty(self) -> None:
        got = rg.shadow_fields(SCAN_ROW, None, 1.0)
        assert got["vulnerability_ids"] is None

    def test_scan_fixture_pages_the_vulnerability_list_in_a_shadow_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        api = _FakeApi(page_size=2)
        monkeypatch.setattr(rg, "_req", api)
        (tmp_path / "f.txt").write_text("x")
        got = rg.scan_fixture("http://api", "t", "team", "node", tmp_path, shadow=True)
        # Two pages for the baseline findings, two more for the shadow record.
        assert api.vuln_calls == 4
        assert got["shadow"]["vulnerability_ids"] == ["CVE-2024-1", "GHSA-xxxx"]
        assert got["vulnerabilities_count"] == 3

    def test_the_baseline_capture_has_no_shadow_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The drift gate compares every key: a shadow key leaking into it
        # would drift every baseline.
        api = _FakeApi()
        monkeypatch.setattr(rg, "_req", api)
        (tmp_path / "f.txt").write_text("x")
        got = rg.scan_fixture("http://api", "t", "team", "node", tmp_path)
        assert "shadow" not in got
        # Every page of findings is read: the baseline records each one.
        assert api.vuln_calls == 2
        assert len(got["vulnerabilities"]) == len(VULN_ROWS)
        assert set(got) == set(json.loads((rg.BASELINE_DIR / "node.json").read_text()))

    def test_shadow_main_records_a_missing_fixture_and_never_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(rg, "login", lambda api: "t")
        monkeypatch.setattr(rg, "_team", lambda api, token: "team")
        out = tmp_path / "out"
        assert rg.shadow_main("http://api", tmp_path / "none", ["node"], out) == 0
        assert json.loads((out / "node.json").read_text()) == {
            "error": "fixture missing",
            "input": {"kind": "source"},
        }


# ---------------------------------------------------------------------------
# Difference table
# ---------------------------------------------------------------------------


def _baseline(name: str) -> dict:
    data: dict = json.loads((rg.BASELINE_DIR / f"{name}.json").read_text())
    return data


def _bomlens_variant_of_maven() -> dict:
    """maven's baseline with one component swapped, one licence and vulns added."""
    bl = copy.deepcopy(_baseline("maven"))
    purls = bl["components"]["purls"]
    purls.remove("pkg:maven/org.apache.commons/commons-lang3@3.12.0?type=jar")
    purls.append("pkg:maven/org.example/extra@1.0?type=jar")
    bl["licenses"].append("BSD-3-Clause|allowed|declared")
    bl["vulnerabilities_count"] = 2
    bl["shadow"] = {
        "vulnerability_ids": ["CVE-2024-9", "CVE-2024-1"],
        "scan": {"status": "succeeded", "stage_outcomes": {"generate": "fallback"},
                 "started_at": "2026-10-12T10:00:00Z", "completed_at": "2026-10-12T10:00:42Z"},
        "wall_seconds": 50.0,
    }
    return bl


def _inprocess_maven() -> dict:
    ip = copy.deepcopy(_baseline("maven"))
    ip["vulnerabilities_count"] = 1
    ip["shadow"] = {
        "vulnerability_ids": ["CVE-2024-1"],
        "scan": {"status": "succeeded", "stage_outcomes": {}, "scancode_skipped": "timeout"},
        "wall_seconds": 30.0,
    }
    return ip


class TestShadowReport:
    def test_a_baseline_against_itself_has_no_difference(self) -> None:
        for name in ("maven", "multi-component", "scancode-mixed-policy"):
            row = sr.compare(name, _baseline(name), _baseline(name))
            assert row.differs is False, name
            assert row.only_bl_purls == [] and row.only_ip_purls == []

    def test_each_side_of_every_difference(self) -> None:
        row = sr.compare("maven", _inprocess_maven(), _bomlens_variant_of_maven())
        assert row.differs is True
        assert row.only_bl_purls == ["pkg:maven/org.example/extra@1.0?type=jar"]
        assert row.only_ip_purls == ["pkg:maven/org.apache.commons/commons-lang3@3.12.0?type=jar"]
        assert row.only_bl_licenses == ["BSD-3-Clause|allowed|declared"]
        assert row.only_ip_licenses == []
        assert row.only_bl_vulns == ["CVE-2024-9"]
        assert row.only_ip_vulns == []

    def test_a_baseline_without_shadow_fields_reads_as_unknown(self) -> None:
        row = sr.compare("maven", _baseline("maven"), _bomlens_variant_of_maven())
        assert row.only_bl_vulns is None
        assert sr.degraded(_baseline("maven")) == []
        assert sr.scan_seconds(_baseline("maven")) is None

    def test_degraded_stages_and_scan_seconds(self) -> None:
        assert sr.degraded(_inprocess_maven()) == ["scancode:timeout"]
        assert sr.degraded(_bomlens_variant_of_maven()) == ["generate:fallback"]
        assert sr.scan_seconds(_bomlens_variant_of_maven()) == 42.0
        # No scan-row times: the harness's wall clock.
        assert sr.scan_seconds(_inprocess_maven()) == 30.0

    def test_a_status_only_difference_still_counts(self) -> None:
        bl = copy.deepcopy(_baseline("node"))
        bl["scan_status"] = "failed"
        assert sr.compare("node", _baseline("node"), bl).differs is True

    def test_the_summary_row(self) -> None:
        report = sr.render(
            [sr.compare("maven", _inprocess_maven(), _bomlens_variant_of_maven())], {}
        )
        row = next(line for line in report.splitlines() if line.startswith("| maven |"))
        cells = [c.strip() for c in row.strip("|").split(" | ")]
        assert cells == [
            "maven", "source", "8 / 8", "1", "1", "1 / 0", "1 / 2", "1 / 0",
            "succeeded / succeeded", "scancode:timeout / generate:fallback",
            "30.0 / 42.0", "pass / pass",
        ]
        assert "  - `BSD-3-Clause|allowed|declared`" in report
        assert "  - `pkg:maven/org.example/extra@1.0?type=jar`" in report

    def test_every_table_row_has_the_header_column_count(self) -> None:
        # Cells carry values from the engine (a stage's reason here); an
        # unescaped `|` in one shifts every column after it.
        ip = _inprocess_maven()
        ip["error"] = "ValueError: a|b"
        bl = _bomlens_variant_of_maven()
        bl["shadow"]["scan"]["stage_outcomes"] = {"generate": "fallback|odd"}
        rows = sr.compare_all({"maven": ip, "node": _baseline("node")}, {"maven": bl})
        report = sr.render(rows, {})
        table = [ln for ln in report.splitlines() if ln.startswith("| ") and "---" not in ln]
        header = table[0]

        def columns(line: str) -> int:
            return len(re.findall(r"(?<!\\)\|", line))

        assert len(table) == 3
        for line in table[1:]:
            assert columns(line) == columns(header), line

    def test_a_fixture_missing_on_one_side(self) -> None:
        row = sr.compare("node", _baseline("node"), None)
        assert row.differs is True
        report = sr.render([row], {})
        assert "| node | source | 1 / missing |" in report
        assert "- No BomLens capture." in report

    def test_long_lists_are_cut(self) -> None:
        bl = copy.deepcopy(_baseline("node"))
        bl["components"]["purls"] = [f"pkg:npm/x{i:03d}@1" for i in range(sr.LIST_CAP + 5)]
        report = sr.render([sr.compare("node", _baseline("node"), bl)], {})
        assert f"PURLs only in BomLens ({sr.LIST_CAP + 5}):" in report
        assert "  - and 5 more" in report
        assert f"`pkg:npm/x{sr.LIST_CAP - 1:03d}@1`" in report
        assert f"`pkg:npm/x{sr.LIST_CAP:03d}@1`" not in report

    @pytest.mark.parametrize(
        ("before", "after", "same"),
        [
            ({"Version": 2, "UpdatedAt": "t1"}, {"Version": 2, "UpdatedAt": "t1"}, True),
            ({"Version": 2, "UpdatedAt": "t1"}, {"Version": 2, "UpdatedAt": "t2"}, False),
            ({"Version": 2, "UpdatedAt": "t1"}, None, False),
            ({"Version": 2}, {"Version": 2}, False),
        ],
    )
    def test_same_db(self, before: dict | None, after: dict | None, same: bool) -> None:
        assert sr.same_db(before, after) is same

    def test_main_over_the_committed_baselines(self, tmp_path: Path) -> None:
        # The baselines are run_golden captures, so both folders can be them.
        trivy = tmp_path / "trivy"
        trivy.mkdir()
        (trivy / "jvm-before.json").write_text('{"Version": 2, "UpdatedAt": "t1"}')
        (trivy / "jvm-after.json").write_text('{"Version": 2, "UpdatedAt": "t2"}')
        out = tmp_path / "report.md"
        code = sr.main([
            "--inprocess", str(rg.BASELINE_DIR), "--bomlens", str(rg.BASELINE_DIR),
            "--trivy-db", str(trivy), "--out", str(out),
        ])
        assert code == 0
        report = out.read_text()
        names = sorted(p.stem for p in rg.BASELINE_DIR.glob("*.json"))
        assert f"{len(names)} fixtures, {len(names)} without any difference" in report
        assert "| jvm | t1 | t2 | NO |" in report
        assert "## Differences" not in report

    def test_main_with_nothing_to_compare_fails(self, tmp_path: Path) -> None:
        assert sr.main(["--inprocess", str(tmp_path / "a"), "--bomlens", str(tmp_path / "b")]) == 1


# ---------------------------------------------------------------------------
# Stage timings
# ---------------------------------------------------------------------------

#: Captures and engine log of run 38223776971 (see PROVENANCE.md there).
RUN = REPO / "apps/backend/tests/fixtures/engine_comparison/run-38223776971"
NODE_SCAN = "77e851f2-c602-4efd-ad9d-96c66a9eea77"
LICENSE_FILES_SCAN = "987b3e90-cc45-4d6e-83bf-aaaf92516168"
#: Not in the engine log: the in-process pass runs no engine container.
INPROCESS_SCAN = "00000000-0000-4000-8000-000000000001"


def _real(side: str, name: str) -> dict:
    data: dict = json.loads((RUN / side / f"{name}.json").read_text())
    return data


def _at(clock: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-12T{clock}+00:00")


def _timed(capture: dict, scan_id: str, stages: list[tuple[str, str]], close: str,
           engine_windows: dict[str, tuple[str, str]] | None = None) -> dict:
    """A copy of a real capture with the fields the run predates.

    ``stage_timings`` is written the way the scan task writes it: one
    ``advance_stage_timings`` per stage, ``close_stage_timings`` at the end and,
    for BomLens, ``add_stage_windows`` with the engine's step windows.
    """
    meta: dict = {}
    for stage, clock in stages:
        meta = scan_outcome.advance_stage_timings(meta, stage=stage, now=_at(clock))
    meta = scan_outcome.close_stage_timings(meta, now=_at(close))
    if engine_windows:
        meta = scan_outcome.add_stage_windows(
            meta, {k: (_at(a), _at(b)) for k, (a, b) in engine_windows.items()}
        )
    out = copy.deepcopy(capture)
    out["shadow"]["scan"]["id"] = scan_id
    out["shadow"]["scan"]["stage_timings"] = meta["stage_timings"]
    return out


def _license_files_inprocess() -> dict:
    # Scan row: 08:31:43.700323 to 08:31:57.920695.
    return _timed(
        _real("inprocess", "scancode-license-files"),
        INPROCESS_SCAN,
        [("bootstrap", "08:31:43.700323"), ("fetch", "08:31:43.760"), ("prep", "08:31:43.900"),
         ("cdxgen", "08:31:44.000"), ("sign", "08:31:47.000"), ("scancode", "08:31:47.100"),
         ("approvals", "08:31:53.100"), ("trivy", "08:31:53.200"), ("finalize", "08:31:57.500")],
        "08:31:57.920695",
    )


def _license_files_bomlens() -> dict:
    # Scan row: 08:33:44.213023 to 08:36:54.133700. prep, cdxgen and scancode
    # open where the runner announces them around the containers in the log.
    return _timed(
        _real("bomlens", "scancode-license-files"),
        LICENSE_FILES_SCAN,
        [("bootstrap", "08:33:44.213023"), ("fetch", "08:33:44.260"), ("prep", "08:33:44.300"),
         ("cdxgen", "08:33:44.733"), ("scancode", "08:36:43.770"), ("sign", "08:36:53.950"),
         ("approvals", "08:36:54.020"), ("finalize", "08:36:54.080")],
        "08:36:54.133700",
        # The live cdxgen and scancode windows stay; trivy and postprocess are
        # the engine's own, inside stage 2.
        engine_windows={
            "cdxgen": ("08:36:42.500", "08:36:43.500"),
            "scancode": ("08:36:44.000", "08:36:46.000"),
            "postprocess": ("08:36:45.000", "08:36:53.400"),
            "trivy": ("08:36:50.000", "08:36:53.000"),
        },
    )


def _table(report: str, first_cell: str) -> list[str]:
    """The table whose header row starts with ``first_cell``."""
    lines = report.splitlines()
    start = lines.index(next(ln for ln in lines if ln.startswith(f"| {first_cell} |")))
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return lines[start + 2:end]


def _rows(report: str, first_cell: str) -> list[list[str]]:
    return [[c.strip() for c in ln.strip("|").split(" | ")] for ln in _table(report, first_cell)]


class TestStageTimingCapture:
    def test_the_scan_id_and_stage_timings_are_kept_as_stored(self) -> None:
        timings = {"cdxgen": {"started_at": "2026-10-12T10:00:00Z",
                              "ended_at": "2026-10-12T10:00:09Z"}}
        row: dict = copy.deepcopy(SCAN_ROW)
        row["metadata"]["stage_timings"] = timings
        got = rg.shadow_fields(row, [], 1.0)["scan"]
        assert got["id"] == "s1"
        assert got["stage_timings"] == timings

    def test_no_stage_timings_is_none(self) -> None:
        assert rg.shadow_fields(SCAN_ROW, [], 1.0)["scan"]["stage_timings"] is None
        row: dict = copy.deepcopy(SCAN_ROW)
        row["metadata"]["stage_timings"] = ["not", "a", "dict"]
        assert rg.shadow_fields(row, [], 1.0)["scan"]["stage_timings"] is None


class TestStageTimingReport:
    def test_engine_log_pairs_each_container_with_its_exit(self) -> None:
        log = sr.load_engine_log(RUN / "engine-log")
        assert len(log) == 6
        node = log[NODE_SCAN]
        assert [c.label for c in node] == ["plan", "stage1", "stage2", "chown"]
        assert all(c.exit_code == 0 for c in node)
        assert node[1].started == _at("08:36:04.267003")
        assert node[1].ended == _at("08:36:06.933548")
        assert [c.label for c in log[LICENSE_FILES_SCAN]] == ["plan", "stage1", "stage2", "chown"]

    def test_engine_log_skips_other_lines(self, tmp_path: Path) -> None:
        (tmp_path / "x.log").write_text(
            "no json here\n"
            '[t] {"event": "bomlens_plan", "scan_id": "a", "timestamp": "2026-10-12T00:00:00Z"}\n'
            '[t] {"event": "bomlens_container_start", "scan_id": "a", '
            '"container": "trusca-bomlens-aa-1-plan", "timestamp": "2026-10-12T00:00:01Z"}\n'
            '[t] {broken\n'
            '[t] {"event": "bomlens_container_exit", "scan_id": "a", "exit_code": 3, '
            '"timestamp": "2026-10-12T00:00:04Z"}\n'
        )
        (only,) = sr.load_engine_log(tmp_path)["a"]
        assert (only.label, only.exit_code) == ("plan", 3)
        assert sr._seconds(only.started, only.ended) == 3.0

    def test_a_window_inside_another_is_labelled_with_the_outermost(self) -> None:
        def w(a: int, b: int) -> tuple[datetime, datetime]:
            return (_at(f"10:00:{a:02d}"), _at(f"10:00:{b:02d}"))

        windows: dict[str, sr.Window] = {
            "cdxgen": w(0, 10), "scancode": w(10, 40), "postprocess": w(12, 38),
            "trivy": w(20, 30), "open": (_at("10:00:05"), None),
        }
        # Back-to-back stages share an instant and are not inside each other.
        assert sr.enclosing(windows) == {"postprocess": "scancode", "trivy": "scancode"}
        # A zero-length window at another's end is not inside it.
        assert sr.enclosing({"a": w(0, 10), "b": w(10, 10)}) == {}

    def test_uncovered_seconds_counts_overlaps_once(self) -> None:
        span = (_at("10:00:00"), _at("10:01:00"))
        windows: dict[str, sr.Window] = {
            "a": (_at("10:00:00"), _at("10:00:20")),
            "b": (_at("10:00:10"), _at("10:00:15")),
            "c": (_at("10:00:30"), _at("10:00:40")),
            "d": (_at("10:00:50"), None),
        }
        assert sr.uncovered_seconds(windows, span) == 30.0

    def test_stage_table_for_a_real_capture_pair(self) -> None:
        log = sr.load_engine_log(RUN / "engine-log")
        row = sr.compare("scancode-license-files", _license_files_inprocess(),
                         _license_files_bomlens())
        report = sr.render([row], {}, log)
        assert _rows(report, "Stage") == [
            ["bootstrap", "0.1", "0%", "0.0", "0%"],
            ["fetch", "0.1", "1%", "0.0", "0%"],
            ["prep", "0.1", "1%", "0.4", "0%"],
            ["cdxgen", "3.0", "21%", "179.0", "94%"],
            ["sign", "0.1", "1%", "0.1", "0%"],
            ["scancode", "6.0", "42%", "10.2", "5%"],
            ["approvals", "0.1", "1%", "0.1", "0%"],
            ["trivy", "4.3", "30%", "3.0 (in scancode)", "2%"],
            ["finalize", "0.4", "3%", "0.1", "0%"],
            ["postprocess", "-", "-", "8.4 (in scancode)", "4%"],
            ["other", "0.0", "0%", "0.0", "0%"],
            ["scan", "14.2", "100%", "189.9", "100%"],
        ]

    def test_container_table_shows_the_wait_before_stage_one(self) -> None:
        # The first scan of the shard waited 177 s between the plan container
        # and stage 1: the cdxgen image check and pull.
        log = sr.load_engine_log(RUN / "engine-log")
        row = sr.compare("scancode-license-files", _license_files_inprocess(),
                         _license_files_bomlens())
        report = sr.render([row], {}, log)
        assert "BomLens engine containers (worker log):" in report
        assert "In-process engine containers" not in report
        assert _rows(report, "Container") == [
            ["plan", "0.4", "0%", "0.0"],
            ["stage1", "1.6", "1%", "177.4"],
            ["stage2", "9.8", "5%", "0.0"],
            ["chown", "0.3", "0%", "0.0"],
        ]

    def test_the_section_sits_between_the_summary_and_the_differences(self) -> None:
        bl = _license_files_bomlens()
        bl["components"]["purls"].append("pkg:pypi/extra@1")
        row = sr.compare("scancode-license-files", _license_files_inprocess(), bl)
        report = sr.render([row], {}, {})
        assert report.index("## Summary") < report.index("## Stage timings")
        assert report.index("## Stage timings") < report.index("### scancode-license-files")
        assert report.index("## Stage timings") < report.index("## Differences")
        # No log: no container table.
        assert "| Container |" not in report

    def test_captures_from_before_the_field_have_no_block(self) -> None:
        row = sr.compare("node", _real("inprocess", "node"), _real("bomlens", "node"))
        report = sr.render([row], {}, sr.load_engine_log(RUN / "engine-log"))
        assert "No capture recorded stage timings." in report
        assert "### node" not in report

    def test_main_reads_the_engine_log(self, tmp_path: Path) -> None:
        # The node capture has its scan id and no stage timings: the container
        # table alone, and no `prep` start to measure the first wait from.
        ip, bl = tmp_path / "ip", tmp_path / "bl"
        ip.mkdir()
        bl.mkdir()
        (ip / "node.json").write_text(json.dumps(_real("inprocess", "node")))
        node = _real("bomlens", "node")
        node["shadow"]["scan"]["id"] = NODE_SCAN
        (bl / "node.json").write_text(json.dumps(node))
        out = tmp_path / "report.md"
        code = sr.main(["--inprocess", str(ip), "--bomlens", str(bl),
                        "--engine-log", str(RUN / "engine-log"), "--out", str(out)])
        assert code == 0
        report = out.read_text()
        assert "| Stage |" not in report
        assert _rows(report, "Container") == [
            ["plan", "0.6", "1%", "-"],
            ["stage1", "2.7", "5%", "38.6"],
            ["stage2", "8.0", "16%", "0.0"],
            ["chown", "1.3", "3%", "0.0"],
        ]

    def test_the_workflow_keeps_the_container_lines_and_passes_them(self) -> None:
        run = _step("Shadow: engine worker log")["run"]
        assert "mkdir -p golden-shadow/engine-log" in run
        assert """grep -E '"event": "bomlens_container_(start|exit)"'""" in run
        assert '>> "golden-shadow/engine-log/${GOLDEN_SHARD}.log"' in run
        # The step runs before the upload, which takes the whole folder.
        names = [s.get("name") for s in _steps()]
        assert names.index("Shadow: engine worker log") < names.index("Upload shadow captures")
        report_job = _workflow()["jobs"]["shadow-report"]
        build = next(s["run"] for s in report_job["steps"]
                     if s.get("name") == "Build the difference table")
        assert "--engine-log golden-shadow/engine-log" in build
