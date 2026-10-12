# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Container images in the engine comparison run.

``golden-nightly.yml`` dispatched with ``shadow=true`` also starts the
``golden-containers`` job: it scans the images in ``container_images.json``
with ``SCAN_CONTAINER_ENGINE=inprocess``, switches the stack to
``SCAN_CONTAINER_ENGINE=bomlens`` and scans them again. The job only runs on
dispatch, so its wiring is checked here, along with the harness's container
capture and the report's container rows, against captures built from the two
engines' real output (``tests/fixtures/engine_comparison/container-alpine``).
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
from services.registry_allowlist import is_registry_allowed, parse_allowed_registries

REPO = Path(__file__).resolve().parents[4]
GOLDEN = REPO / "apps/backend/tests/e2e/golden"
WORKFLOW = REPO / ".github/workflows/golden-nightly.yml"
OVERLAY = GOLDEN / "compose.containers.yml"
CAPTURES = REPO / "apps/backend/tests/fixtures/engine_comparison/container-alpine"
ALPINE = (
    "docker.io/library/alpine:3.19.0"
    "@sha256:51b67269f354137895d43f3b3d810bfacd3945438e94dc5ac55fdac340352f48"
)

sys.path.insert(0, str(GOLDEN))
import run_golden as rg  # noqa: E402
import shadow_report as sr  # noqa: E402


def _load(path: Path) -> dict:
    data: dict = yaml.safe_load(path.read_text())
    return data


def _job() -> dict:
    job: dict = _load(WORKFLOW)["jobs"]["golden-containers"]
    return job


def _step(prefix: str) -> dict:
    return next(s for s in _job()["steps"] if s.get("name", "").startswith(prefix))


def _real(side: str) -> dict:
    data: dict = json.loads((CAPTURES / side / "container-alpine.json").read_text())
    return data


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class TestJob:
    def test_it_runs_only_in_a_dispatched_comparison(self) -> None:
        # Job-level: the nightly schedule never starts a runner for it.
        assert _job()["if"] == (
            "${{ github.event_name == 'workflow_dispatch' && "
            "github.event.inputs.shadow == 'true' }}"
        )

    def test_the_gate_job_has_no_container_shard(self) -> None:
        golden = _load(WORKFLOW)["jobs"]["golden"]
        shards = golden["strategy"]["matrix"]["include"]
        names = {n for shard in shards for n in shard["names"].split()}
        assert not names & {i["name"] for i in rg.load_images()}
        assert "--images" not in json.dumps(golden["steps"])

    def test_the_first_pass_dispatches_in_process(self) -> None:
        env = _job()["env"]
        assert env["SCAN_CONTAINER_ENGINE"] == "inprocess"
        assert env["COMPOSE_FILE"] == (
            "docker-compose.dev.yml:apps/backend/tests/e2e/golden/compose.containers.yml"
        )
        run = _step("Bring up backend + worker")["run"]
        assert "printenv SCAN_CONTAINER_ENGINE" in run
        assert '[ "$engine" != "inprocess" ]' in run

    def test_both_passes_scan_the_image_list_and_record(self) -> None:
        assert _job()["env"]["GOLDEN_IMAGES"] == str(rg.IMAGE_LIST.relative_to(REPO))
        for prefix, folder in (
            ("Comparison pass 1", "inprocess"),
            ("Comparison pass 2", "bomlens"),
        ):
            run = _step(prefix)["run"]
            assert '--images "$GOLDEN_IMAGES"' in run
            assert f"--shadow-out golden-shadow/{folder}" in run
            assert "--update" not in run and "--fixtures" not in run

    def test_the_switch_recreates_the_backend_with_the_bomlens_engine(self) -> None:
        switch = _step("Comparison: switch the stack")
        assert switch["env"]["SCAN_CONTAINER_ENGINE"] == "bomlens"
        assert switch["env"]["COMPOSE_PROFILES"] == "bomlens-engine"
        assert switch["env"]["COMPOSE_FILE"] == (
            _job()["env"]["COMPOSE_FILE"] + ":apps/backend/tests/e2e/golden/compose.bomlens.yml"
        )
        lines = [line.strip() for line in switch["run"].splitlines()]
        remove = lines.index("docker-compose rm -fs backend")
        assert lines[remove + 1] == "docker-compose up -d --no-deps backend"
        assert "docker-compose up -d --no-deps docker-socket-proxy worker-engine" in lines
        # The setting is read through the backend's own function, so a
        # backend that does not know it fails here instead of scanning
        # in-process twice.
        assert "from core.config import scan_container_engine" in switch["run"]
        assert '[ "$engine" != "bomlens" ]' in switch["run"]

    def test_the_switch_keeps_the_source_shards_safeguards(self) -> None:
        # The two switch steps are separate scripts; the lines that keep the
        # engine isolated and on the same Trivy database must be in both.
        source = next(
            s["run"] for s in _load(WORKFLOW)["jobs"]["golden"]["steps"]
            if s.get("name", "").startswith("Shadow: switch the stack")
        )
        mine = _step("Comparison: switch the stack")["run"]
        for line in (
            '{{range .Mounts}}{{if eq .Destination "/var/lib/trivy"}}{{.Name}}{{end}}{{end}}',
            '{{range .Mounts}}{{if eq .Destination "/tmp/trustedoss"}}{{.Name}}{{end}}{{end}}',
            "(umask 077 && docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}'",
            "grep -Eq '^SCAN_BOMLENS_(DEEP_LICENSE_)?IMAGE=.' \"$GOLDEN_ENGINE_ENV_FILE\"",
            "docker network create --opt com.docker.network.bridge.enable_icc=false "
            "trustedoss-scan-egress",
            '[ "$icc" != "false" ]',
        ):
            assert line in source, line
            assert line in mine, line

    def test_the_engine_worker_is_checked_to_receive_the_allow_list(self) -> None:
        run = _step("Comparison: switch the stack")["run"]
        assert (
            'grep -qxF "CONTAINER_SCAN_ALLOWED_REGISTRIES=$CONTAINER_SCAN_ALLOWED_REGISTRIES" '
            '"$GOLDEN_ENGINE_ENV_FILE"'
        ) in run

    def test_only_the_bomlens_image_is_pulled_beforehand(self) -> None:
        # The scanned images are not: neither engine reads the runner's Docker.
        pulls = [
            line.strip() for s in _job()["steps"] for line in s.get("run", "").splitlines()
            if "docker pull" in line
        ]
        assert pulls == ['docker pull --quiet "$ref"']
        assert "from core.config import bomlens_image; print(bomlens_image())" in (
            _step("Comparison: switch the stack")["run"]
        )

    def test_the_captures_reach_the_report(self) -> None:
        upload = _step("Upload the comparison captures")
        assert upload["if"] == "always()"
        assert upload["with"]["name"] == "golden-shadow-${{ env.GOLDEN_SHARD }}"
        assert upload["with"]["path"] == "golden-shadow/"
        report = _load(WORKFLOW)["jobs"]["shadow-report"]
        download = next(s for s in report["steps"] if "download-artifact" in s.get("uses", ""))
        assert download["with"]["pattern"] == "golden-shadow-*"
        assert "golden-containers" in report["needs"]
        # Trivy metadata and engine log under the job's own shard name.
        assert _job()["env"]["GOLDEN_SHARD"] == "containers"
        assert '"golden-shadow/trivy-db/${GOLDEN_SHARD}-before.json"' in (
            _step("Comparison: wait for the Trivy database")["run"]
        )
        assert '"golden-shadow/trivy-db/${GOLDEN_SHARD}-after.json"' in (
            _step("Comparison: Trivy database after both passes")["run"]
        )
        assert """grep -E '"event": "bomlens_container_(start|exit)"'""" in (
            _step("Comparison: engine worker log")["run"]
        )

    def test_teardown_includes_the_engine_services(self) -> None:
        down = _step("Tear down")
        assert down["if"] == "always()"
        assert down["env"]["COMPOSE_FILE"].endswith("compose.bomlens.yml")
        assert "docker network rm trustedoss-scan-egress" in down["run"]


class TestOverlay:
    def test_it_sets_two_keys_on_two_services_of_the_dev_file(self) -> None:
        overlay = _load(OVERLAY)["services"]
        dev = _load(REPO / "docker-compose.dev.yml")["services"]
        assert set(overlay) == {"backend", "celery-worker"} <= set(dev)
        assert set(overlay["backend"]["environment"]) == {
            "CONTAINER_SCAN_ALLOWED_REGISTRIES", "SCAN_CONTAINER_ENGINE",
        }
        assert set(overlay["celery-worker"]["environment"]) == {"CONTAINER_SCAN_ALLOWED_REGISTRIES"}

    def test_an_unset_allow_list_stops_compose(self) -> None:
        # An empty list allows every registry; the run must not fall back to it.
        for service in ("backend", "celery-worker"):
            value = _load(OVERLAY)["services"][service]["environment"][
                "CONTAINER_SCAN_ALLOWED_REGISTRIES"
            ]
            assert value.startswith("${CONTAINER_SCAN_ALLOWED_REGISTRIES:?"), service
        engine = _load(OVERLAY)["services"]["backend"]["environment"]["SCAN_CONTAINER_ENGINE"]
        assert engine == "${SCAN_CONTAINER_ENGINE:-inprocess}"

    def test_the_dev_file_does_not_pass_the_allow_list_itself(self) -> None:
        # Why the overlay exists. If the dev file starts passing it, this
        # overlay's line for it can go.
        dev = (REPO / "docker-compose.dev.yml").read_text()
        assert "CONTAINER_SCAN_ALLOWED_REGISTRIES" not in dev


# ---------------------------------------------------------------------------
# Image list
# ---------------------------------------------------------------------------


class TestImageList:
    def test_the_committed_list(self) -> None:
        images = rg.load_images()
        assert [i["name"] for i in images] == [
            "container-nginx-alpine", "container-debian-slim", "container-node-slim",
        ]
        for image in images:
            assert re.search(r"@sha256:[0-9a-f]{64}$", image["ref"]), image["name"]
            assert "linux/amd64" in image["platforms"], image["name"]
            assert image["why"], image["name"]

    def test_the_workflow_allow_list_admits_every_image_and_only_those_registries(self) -> None:
        allowed = parse_allowed_registries(_job()["env"]["CONTAINER_SCAN_ALLOWED_REGISTRIES"])
        assert allowed
        for image in rg.load_images():
            assert is_registry_allowed(image["ref"], allowed), image["ref"]
        assert not is_registry_allowed("ghcr.io/library/nginx:1", allowed)
        assert not is_registry_allowed("docker.io/someone/nginx:1", allowed)

    @pytest.mark.parametrize(
        ("images", "message"),
        [
            ([], "lists no images"),
            ([{"name": "a", "ref": "docker.io/library/a:1"}], "not pinned"),
            ([{"name": "a", "ref": ALPINE}, {"name": "a", "ref": ALPINE}], "repeated name"),
            ([{"ref": ALPINE}], "missing or repeated name"),
        ],
    )
    def test_a_bad_list_is_refused(self, tmp_path: Path, images: list, message: str) -> None:
        path = tmp_path / "list.json"
        path.write_text(json.dumps({"images": images}))
        with pytest.raises(ValueError, match=message):
            rg.load_images(path)

    def test_select_images(self) -> None:
        images = rg.load_images()
        assert rg.select_images(images, []) == images
        assert [i["name"] for i in rg.select_images(images, ["container-node-slim"])] == [
            "container-node-slim"
        ]
        with pytest.raises(ValueError, match="container-nope"):
            rg.select_images(images, ["container-nope"])


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


SCAN_ROW = {
    "id": "s1",
    "kind": "container",
    "status": "succeeded",
    "started_at": "2026-10-12T14:52:09Z",
    "completed_at": "2026-10-12T14:52:21Z",
    "error_message": None,
    "metadata": {"image_ref": ALPINE},
}


class _FakeApi:
    """Answers the harness's calls for one project and one container scan."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, object]] = []

    def __call__(self, method, url, *, token=None, json_body=None, raw_body=None, ctype=None):
        path = url.split("http://api", 1)[1]
        if method == "POST":
            self.posts.append((path, json_body if json_body is not None else raw_body))
        if method == "POST" and path == "/v1/projects":
            return 201, json.dumps({"id": "p1"}).encode(), {}
        if method == "POST" and path == "/v1/projects/p1/scans":
            return 202, json.dumps({"id": "s1"}).encode(), {}
        if path == "/v1/scans/s1":
            return 200, json.dumps(SCAN_ROW).encode(), {}
        if path == "/v1/projects/p1/vulnerabilities":
            return 200, json.dumps({"items": [], "total": 1}).encode(), {}
        if path.startswith("/v1/projects/p1/vulnerabilities?"):
            item = {"cve_id": "CVE-2024-1", "affected_component_name": "musl",
                    "affected_component_version": "1.2.4-r2"}
            return 200, json.dumps({"items": [item], "total": 1}).encode(), {}
        if path.startswith("/v1/projects/p1/components"):
            body = {"items": [{"purl": "pkg:apk/alpine/musl@1.2.4-r2"}], "total": 1}
            return 200, json.dumps(body).encode(), {}
        return 404, b"{}", {}


class TestHarness:
    def test_scan_image_triggers_a_container_scan_and_captures_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = _FakeApi()
        monkeypatch.setattr(rg, "_req", api)
        got = rg.scan_image("http://api", "t", "team", "container-x", ALPINE)
        paths = [p for p, _ in api.posts]
        # No archive upload: the project, then the scan.
        assert paths == ["/v1/projects", "/v1/projects/p1/scans"]
        assert api.posts[1][1] == {"kind": "container", "metadata": {"image_ref": ALPINE}}
        assert got["scan_status"] == "succeeded"
        assert got["components"]["purls"] == ["pkg:apk/alpine/musl@1.2.4-r2"]
        assert got["shadow"]["vulnerability_ids"] == ["CVE-2024-1"]
        assert got["shadow"]["scan"]["kind"] == "container"
        assert got["shadow"]["scan"]["engine"] == "inprocess"
        # The same keys as a source capture.
        baseline = json.loads((rg.BASELINE_DIR / "node.json").read_text())
        assert set(got) == set(baseline) | {"shadow"}

    def test_image_main_records_the_input_on_every_capture(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(rg, "login", lambda api: "t")
        monkeypatch.setattr(rg, "_team", lambda api, token: "team")
        calls: list[str] = []

        def scan_image(api, token, team, name, ref):  # type: ignore[no-untyped-def]
            calls.append(name)
            if name == "bad":
                raise RuntimeError("trigger failed 422: b'registry not allowed'")
            return {"scan_status": "succeeded"}

        monkeypatch.setattr(rg, "scan_image", scan_image)
        images = [{"name": "good", "ref": ALPINE}, {"name": "bad", "ref": ALPINE}]
        assert rg.image_main("http://api", images, tmp_path) == 0
        assert calls == ["good", "bad"]
        good = json.loads((tmp_path / "good.json").read_text())
        bad = json.loads((tmp_path / "bad.json").read_text())
        assert good == {"scan_status": "succeeded",
                        "input": {"kind": "container", "image_ref": ALPINE}}
        assert bad["input"] == {"kind": "container", "image_ref": ALPINE}
        assert bad["error"].startswith("RuntimeError: trigger failed 422")

    @pytest.mark.parametrize(
        "argv",
        [
            ["--images", "x.json"],
            ["--images", "x.json", "--shadow-out", "o", "--fixtures", "f"],
            ["--images", "x.json", "--shadow-out", "o", "--update"],
            ["--names", "node"],
        ],
    )
    def test_images_are_only_scanned_for_the_comparison(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["run_golden.py", *argv])
        with pytest.raises(SystemExit) as exc:
            rg.main()
        assert exc.value.code == 2

    def test_main_passes_the_selected_images(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        seen: list[tuple[str, list[str], Path]] = []

        def image_main(api: str, images: list[dict], out: Path) -> int:
            seen.append((api, [i["name"] for i in images], out))
            return 0

        monkeypatch.setattr(rg, "image_main", image_main)
        monkeypatch.setattr(sys, "argv", [
            "run_golden.py", "--api", "http://api", "--images", str(rg.IMAGE_LIST),
            "--shadow-out", str(tmp_path), "--names", "container-debian-slim",
        ])
        assert rg.main() == 0
        assert seen == [("http://api", ["container-debian-slim"], tmp_path)]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _at(clock: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-12T{clock}+00:00")


def _timed(capture: dict, scan_id: str, stages: list[tuple[str, str]], close: str,
           engine_windows: dict[str, tuple[str, str]] | None = None) -> dict:
    """A copy of a capture with a scan id, scan times and stage windows.

    Written through the backend's writers, the way the container task writes
    them. The times are illustrative: no run has scanned an image yet. The
    engine windows are the ``image-registry-alpine`` run's own step times
    (``generate`` 14:52:13 to :17, the enrich and conformance steps :17 to
    :18, ``scan-security`` :18 to :19), as ``stages.stage_windows`` folds
    them.
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
    scan = out["shadow"]["scan"]
    scan["id"] = scan_id
    scan["started_at"] = _at(stages[0][1]).isoformat()
    scan["completed_at"] = _at(close).isoformat()
    scan["stage_timings"] = meta["stage_timings"]
    return out


def _inprocess() -> dict:
    return _timed(
        _real("inprocess"), "ip-scan",
        [("bootstrap", "14:40:00.000"), ("trivy", "14:40:00.100"),
         ("persist", "14:40:08.600"), ("finalize", "14:40:09.400")],
        "14:40:09.500",
    )


def _bomlens() -> dict:
    return _timed(
        _real("bomlens"), "bl-scan",
        [("bootstrap", "14:52:09.000"), ("trivy", "14:52:09.200"),
         ("persist", "14:52:20.200"), ("finalize", "14:52:20.800")],
        "14:52:21.000",
        engine_windows={
            "cdxgen": ("14:52:13", "14:52:17"),
            "postprocess": ("14:52:17", "14:52:18"),
            # Dropped by add_stage_windows: the live trivy window stays.
            "trivy": ("14:52:18", "14:52:19"),
        },
    )


def _rows(report: str, first_cell: str) -> list[list[str]]:
    lines = report.splitlines()
    start = lines.index(next(ln for ln in lines if ln.startswith(f"| {first_cell} |")))
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return [[c.strip() for c in ln.strip("|").split(" | ")] for ln in lines[start + 2:end]]


class TestReport:
    def test_the_real_pair_differs_only_in_how_licences_are_kept(self) -> None:
        row = sr.compare("container-alpine", _real("inprocess"), _real("bomlens"))
        assert row.kind == "container"
        assert row.image == ALPINE
        assert row.only_bl_purls == [] and row.only_ip_purls == []
        assert row.only_bl_vulns == [] and row.only_ip_vulns == []
        assert row.only_bl_licenses == [
            "BSD-2-Clause AND BSD-3-Clause|allowed|declared",
            "MIT AND BSD-2-Clause AND GPL-2.0-or-later|forbidden|declared",
            "MPL-2.0 AND MIT|conditional|declared",
        ]
        assert row.only_ip_licenses == [
            "BSD-2-Clause|allowed|declared",
            "BSD-3-Clause|allowed|declared",
            "GPL-2.0-or-later|forbidden|declared",
            "MPL-2.0|conditional|declared",
        ]
        assert row.differs is True

    def test_the_summary_row_names_the_input(self) -> None:
        report = sr.render([sr.compare("container-alpine", _inprocess(), _bomlens())], {})
        (cells,) = [r for r in _rows(report, "Fixture") if r[0] == "container-alpine"]
        assert cells == [
            "container-alpine", "container", "15 / 15", "0", "0", "3 / 4", "48 / 48",
            "0 / 0", "succeeded / succeeded", "none / none", "9.5 / 12.0", "- / -",
        ]

    def test_the_images_section_lists_each_reference(self) -> None:
        rows = sr.compare_all({"container-alpine": _real("inprocess")},
                              {"container-alpine": _real("bomlens")})
        report = sr.render(rows, {})
        assert report.index("## Container images") < report.index("## Summary")
        assert _rows(report, "Image") == [["container-alpine", f"`{ALPINE}`"]]
        assert "Nothing is pulled into the runner's Docker beforehand" in report

    def test_no_images_section_for_a_source_only_run(self) -> None:
        node = json.loads((rg.BASELINE_DIR / "node.json").read_text())
        report = sr.render([sr.compare("node", node, node)], {})
        assert "## Container images" not in report
        assert "A container scan has the stages" not in report

    def test_sources_come_first_then_images(self) -> None:
        node = json.loads((rg.BASELINE_DIR / "node.json").read_text())
        ip = {"container-alpine": _real("inprocess"), "node": node, "zz-source": node}
        bl = {"container-alpine": _real("bomlens"), "node": node}
        rows = sr.compare_all(ip, bl)
        assert [(r.name, r.kind) for r in rows] == [
            ("node", "source"), ("zz-source", "source"), ("container-alpine", "container"),
        ]

    def test_a_failed_image_capture_keeps_its_kind(self) -> None:
        failed = {"error": "RuntimeError: x", "input": {"kind": "container", "image_ref": ALPINE}}
        row = sr.compare("container-alpine", failed, None)
        assert row.kind == "container"
        assert row.image == ALPINE

    def test_stage_table_for_a_container_pair(self) -> None:
        row = sr.compare("container-alpine", _inprocess(), _bomlens())
        report = sr.render([row], {}, {})
        assert _rows(report, "Stage") == [
            ["bootstrap", "0.1", "1%", "0.2", "2%"],
            ["trivy", "8.5", "89%", "11.0", "92%"],
            ["persist", "0.8", "8%", "0.6", "5%"],
            ["finalize", "0.1", "1%", "0.2", "2%"],
            ["cdxgen", "-", "-", "4.0 (in trivy)", "33%"],
            ["postprocess", "-", "-", "1.0 (in trivy)", "8%"],
            ["other", "0.0", "0%", "0.0", "0%"],
            ["scan", "9.5", "100%", "12.0", "100%"],
        ]
        assert "A container scan has the stages" in report

    def test_the_engine_container_of_an_image_scan(self, tmp_path: Path) -> None:
        (tmp_path / "containers.log").write_text(
            '[t] {"event": "bomlens_container_start", "scan_id": "bl-scan", '
            '"container": "trusca-bomlens-00ff-1-image", "stage": "trivy", '
            '"timestamp": "2026-10-12T14:52:12.500000Z"}\n'
            '[t] {"event": "bomlens_container_exit", "scan_id": "bl-scan", "exit_code": 0, '
            '"timestamp": "2026-10-12T14:52:19.800000Z"}\n'
        )
        log = sr.load_engine_log(tmp_path)
        report = sr.render([sr.compare("container-alpine", _inprocess(), _bomlens())], {}, log)
        # No prep stage on a container scan: the first wait is not measured.
        assert _rows(report, "Container") == [["image", "7.3", "61%", "-"]]

    def test_main_over_the_real_pair(self, tmp_path: Path) -> None:
        ip, bl, trivy = tmp_path / "ip", tmp_path / "bl", tmp_path / "trivy"
        for folder, side in ((ip, "inprocess"), (bl, "bomlens")):
            folder.mkdir()
            (folder / "container-alpine.json").write_text(json.dumps(_real(side)))
        trivy.mkdir()
        (trivy / "containers-before.json").write_text('{"Version": 2, "UpdatedAt": "t1"}')
        (trivy / "containers-after.json").write_text('{"Version": 2, "UpdatedAt": "t1"}')
        out = tmp_path / "report.md"
        assert sr.main(["--inprocess", str(ip), "--bomlens", str(bl),
                        "--trivy-db", str(trivy), "--out", str(out)]) == 0
        report = out.read_text()
        assert "| containers | t1 | t1 | yes |" in report
        assert "1 fixtures, 0 without any difference" in report
        assert "  - `MPL-2.0 AND MIT|conditional|declared`" in report
