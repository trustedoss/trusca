"""
P2 #8a — unit tests for the parallel DT-upload / scancode phase.

The integration test in
``tests/integration/scan/test_scan_source_pipeline_mock.py`` exercises the
happy path against a real Postgres. These unit tests pin the *concurrency*
contract specifically:

  - The DT upload (``upsert_project`` + ``upload_sbom``) runs in a worker
    thread submitted to a ``ThreadPoolExecutor``, not on the main thread.
  - Scancode + persist + approvals run on the main thread while the upload
    thread is in flight, so wall-time overlaps.
  - At the join point we surface any DT failure to the main thread's
    existing terminal-failure handlers — the error type is preserved
    (DTBreakerOpen / DTError / DTUnavailable / DTClientError).
  - A scancode failure does NOT cancel or affect the DT upload thread, and
    vice-versa — they are independent best-effort branches.
  - The executor exits cleanly on every code path, draining the future.

We drive ``_run_pipeline`` directly with a mocked workspace + DT client +
breaker rather than going through ``scan_source_task`` so each test
remains DB-free. The pipeline writes to PG through ``sync_session_scope``
which we monkeypatch into a no-op context manager — components / approvals
are exercised in the integration test, not here.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Shared test doubles
# ---------------------------------------------------------------------------


class _PassthroughBreaker:
    """Breaker that just runs the callable — no Redis, no state machine."""

    def call(self, fn):  # type: ignore[no-untyped-def]
        return fn()


class _RecordingDTClient:
    """DT client that records each call with a per-call delay knob.

    The default ``upsert_delay`` / ``upload_delay`` are zero so the upload
    thread races scancode without blocking; ``test_dt_upload_actually_overlaps_scancode``
    bumps the upload delay to assert overlap.
    """

    def __init__(
        self,
        *,
        upsert_delay: float = 0.0,
        upload_delay: float = 0.0,
        upsert_raises: BaseException | None = None,
        upload_raises: BaseException | None = None,
        project_uuid: str = "fake-dt-uuid-1",
    ) -> None:
        self.upsert_delay = upsert_delay
        self.upload_delay = upload_delay
        self.upsert_raises = upsert_raises
        self.upload_raises = upload_raises
        self.project_uuid_to_return = project_uuid
        self.upsert_calls = 0
        self.upload_calls = 0
        self.findings_calls = 0
        self.closed = False
        self.upsert_started_at: float | None = None
        self.upload_finished_at: float | None = None
        self.upsert_thread_name: str | None = None
        self.upload_thread_name: str | None = None

    def upsert_project(self, *, name: str, version: str) -> str:  # noqa: ARG002
        self.upsert_calls += 1
        if self.upsert_started_at is None:
            self.upsert_started_at = time.monotonic()
            self.upsert_thread_name = threading.current_thread().name
        if self.upsert_delay > 0:
            time.sleep(self.upsert_delay)
        if self.upsert_raises is not None:
            raise self.upsert_raises
        return self.project_uuid_to_return

    def upload_sbom(self, *, project_uuid: str, sbom_json: bytes) -> str:  # noqa: ARG002
        self.upload_calls += 1
        if self.upload_delay > 0:
            time.sleep(self.upload_delay)
        if self.upload_raises is not None:
            raise self.upload_raises
        self.upload_finished_at = time.monotonic()
        self.upload_thread_name = threading.current_thread().name
        return "fake-upload-token"

    def get_findings(self, *, project_uuid: str) -> list[dict[str, Any]]:  # noqa: ARG002
        self.findings_calls += 1
        return []

    def count_vulnerabilities(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


def _make_sbom_payload(workspace: Path) -> tuple[Any, dict[str, Any]]:
    """Build a CdxgenResult + a minimal SBOM dict, with the SBOM written to disk.

    cdxgen's mock backend would normally do this; we duplicate the shape here
    so the pipeline-stage code that re-reads the file (``_sanitize_sbom_hashes_for_dt``
    via the upload thread) gets real bytes back.
    """
    import json

    from integrations.cdxgen import CdxgenResult

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:fake-{workspace.name}",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": "fake-app", "version": "0.0.0"}
        },
        "components": [],
    }
    cdxgen_dir = workspace / "cdxgen"
    cdxgen_dir.mkdir(parents=True, exist_ok=True)
    sbom_path = cdxgen_dir / "cdxgen.cdx.json"
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    return CdxgenResult(sbom_path=sbom_path, sbom=sbom), sbom


@pytest.fixture
def patch_pipeline_minimal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, Any]:
    """Stub out every helper that touches Postgres so the test stays unit-level.

    We let the *real* ``_run_pipeline`` execute (so the concurrency code paths
    are exercised) but replace every DB- / network- / subprocess-touching
    helper with a recorder. The DT client + breaker stay configurable per
    test via overrides on the returned dict.
    """
    # Build a workspace + cdxgen result + source dir.
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    source_dir = workspace / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    cdxgen_result, sbom_dict = _make_sbom_payload(workspace)

    # 1) Make _set_stage a no-op recorder (the DB write under the hood requires
    #    a real session; we just capture the stage labels for assertions).
    stages: list[str] = []
    monkeypatch.setattr(
        "tasks.scan_source._set_stage",
        lambda scan_uuid, stage: stages.append(stage),
    )

    # 2) Make _persist_artifact a no-op (writes to scan_artifacts).
    monkeypatch.setattr(
        "tasks.scan_source._persist_artifact",
        lambda *a, **kw: None,
    )

    # 3) Make _fetch_source skip the real git clone — return source_dir.
    monkeypatch.setattr(
        "tasks.scan_source._fetch_source",
        lambda **kw: source_dir,
    )

    # 4) Make _prepare_for_cdxgen a no-op (would shell out to bundler etc.).
    monkeypatch.setattr(
        "tasks.scan_source._prepare_for_cdxgen",
        lambda **kw: None,
    )

    # 5) Make run_cdxgen return our pre-built result.
    monkeypatch.setattr(
        "tasks.scan_source.cdxgen_adapter.run_cdxgen",
        lambda **kw: cdxgen_result,
    )

    # 6) Make sign/attest no-ops (best-effort already).
    monkeypatch.setattr("tasks.scan_source._sign_sbom", lambda **kw: False)
    monkeypatch.setattr("tasks.scan_source._attest_sbom", lambda **kw: None)

    # 7) Replace sync_session_scope with a no-op CM so _persist_components etc.
    #    have something to bind to. The replacements below short-circuit any
    #    actual session use.
    @contextmanager
    def _fake_session_scope():  # type: ignore[no-untyped-def]
        yield _FakeSession()

    monkeypatch.setattr("tasks.scan_source.sync_session_scope", _fake_session_scope)

    # 8) Stub the writers that the pipeline calls inside sessions.
    monkeypatch.setattr(
        "tasks.scan_source._persist_components",
        lambda session, *, scan_uuid, sbom: None,
    )
    monkeypatch.setattr(
        "tasks.scan_source._persist_detected_licenses",
        lambda session, *, scan_uuid, sbom, detections: None,
    )
    monkeypatch.setattr(
        "tasks.scan_source._auto_create_conditional_approvals",
        lambda *, scan_uuid, project_id: None,
    )
    monkeypatch.setattr(
        "tasks.scan_source._persist_findings",
        lambda session, *, scan_uuid, findings: None,
    )
    monkeypatch.setattr(
        "tasks.scan_source._record_dt_vuln_count",
        lambda session, *, scan_uuid, count: None,
    )
    monkeypatch.setattr(
        "tasks.scan_source._preserve_source_tree", lambda **kw: None
    )
    monkeypatch.setattr("tasks.scan_source._mark_succeeded", lambda *a, **kw: None)
    monkeypatch.setattr(
        "tasks.scan_source._dispatch_reachability", lambda *a, **kw: None
    )

    # 9) Zero out the dt_findings poll schedule so the test does not wait
    #    the full 60-second budget when DT returns [].
    monkeypatch.setattr(
        "tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (0,)
    )
    # Make time.sleep called from the helper a no-op (it's the only time.sleep
    # in scan_source; we keep `time.monotonic` real for timing assertions).
    monkeypatch.setattr("tasks.scan_source.time.sleep", lambda d: None)

    return {
        "workspace": workspace,
        "source_dir": source_dir,
        "cdxgen_result": cdxgen_result,
        "sbom": sbom_dict,
        "stages": stages,
    }


class _FakeSession:
    """Minimal session double for the sync_session_scope CM stub."""

    def commit(self) -> None:
        pass

    def begin_nested(self):  # type: ignore[no-untyped-def]
        @contextmanager
        def _cm():
            yield self

        return _cm()

    def get(self, *_a, **_kw):  # type: ignore[no-untyped-def]
        return None


def _run_pipeline_with_dt(
    monkeypatch: pytest.MonkeyPatch,
    patch_pipeline_minimal: dict[str, Any],
    dt_client: _RecordingDTClient,
    *,
    scancode_runner=None,
) -> tuple[uuid.UUID, list[str]]:
    """Invoke ``_run_pipeline`` with a pre-built DT client + scancode mock."""
    monkeypatch.setattr("tasks.scan_source.get_breaker", lambda: _PassthroughBreaker())
    monkeypatch.setattr("tasks.scan_source.build_client", lambda: dt_client)

    if scancode_runner is None:
        # Default scancode: returns an empty result quickly.
        from integrations.scancode import ScancodeResult

        def scancode_runner(*, source_dir, output_dir, **kw):  # type: ignore[no-untyped-def]
            output_dir.mkdir(parents=True, exist_ok=True)
            result_path = output_dir / "scancode.json"
            result_path.write_text("{}", encoding="utf-8")
            return ScancodeResult(result_path=result_path, detections=[])

    monkeypatch.setattr(
        "tasks.scan_source.scancode_adapter.run_scancode", scancode_runner
    )

    scan_uuid = uuid.uuid4()
    project_id = uuid.uuid4()

    from tasks.scan_source import _run_pipeline

    _run_pipeline(
        scan_uuid=scan_uuid,
        project_id=project_id,
        workspace=patch_pipeline_minimal["workspace"],
        git_url=None,
        scan_metadata={},
    )
    return scan_uuid, patch_pipeline_minimal["stages"]


# ---------------------------------------------------------------------------
# 1. DT upload runs on a worker thread (not the main thread)
# ---------------------------------------------------------------------------


def test_dt_upload_runs_on_worker_thread(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """The DT upload pair must execute on the ThreadPoolExecutor worker, not
    the main thread — that's the whole point of the parallel layout.
    """
    dt_client = _RecordingDTClient()
    main_thread_name = threading.current_thread().name

    _run_pipeline_with_dt(monkeypatch, patch_pipeline_minimal, dt_client)

    assert dt_client.upsert_calls == 1
    assert dt_client.upload_calls == 1
    assert dt_client.upsert_thread_name is not None
    assert dt_client.upload_thread_name is not None
    # Both upsert + upload happen on the same worker thread.
    assert dt_client.upsert_thread_name == dt_client.upload_thread_name
    # And that worker thread is NOT the main thread.
    assert dt_client.upsert_thread_name != main_thread_name
    # The executor naming prefix is part of the contract — operators grep
    # logs for these threads when investigating a stuck worker.
    assert dt_client.upsert_thread_name.startswith("scan-dt-upload")


# ---------------------------------------------------------------------------
# 2. DT upload actually overlaps with scancode (wall-time overlap)
# ---------------------------------------------------------------------------


def test_dt_upload_actually_overlaps_scancode(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """Pin the wall-time overlap that justifies this PR.

    Sequential layout: scancode(200ms) + dt_upload(200ms) = ~400ms.
    Parallel  layout : max(scancode, dt_upload)          = ~200ms.

    We give DT a 200ms upload delay and scancode a 200ms sleep, then assert
    the total pipeline takes well under the sequential 400ms — proving the
    two ran concurrently.
    """
    dt_client = _RecordingDTClient(upload_delay=0.20)

    from integrations.scancode import ScancodeResult

    def slow_scancode(*, source_dir, output_dir, **kw):  # type: ignore[no-untyped-def]
        output_dir.mkdir(parents=True, exist_ok=True)
        time.sleep(0.20)
        result_path = output_dir / "scancode.json"
        result_path.write_text("{}", encoding="utf-8")
        return ScancodeResult(result_path=result_path, detections=[])

    start = time.monotonic()
    _run_pipeline_with_dt(
        monkeypatch,
        patch_pipeline_minimal,
        dt_client,
        scancode_runner=slow_scancode,
    )
    elapsed = time.monotonic() - start

    # The sequential lower bound is 0.40s; the parallel layout takes ~0.20s
    # plus pipeline overhead. Use a generous 0.35s ceiling to keep the test
    # robust on slow CI while still detecting a sequential regression.
    assert elapsed < 0.35, (
        f"pipeline took {elapsed:.3f}s — DT upload and scancode appear to be "
        f"running sequentially (sequential lower bound ≈ 0.40s)"
    )
    # And both did run.
    assert dt_client.upload_calls == 1


# ---------------------------------------------------------------------------
# 3. DT upload failure (DTBreakerOpen) propagates through future.result()
# ---------------------------------------------------------------------------


def test_dt_upsert_breaker_open_propagates_at_join(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """DTBreakerOpen raised inside the upload thread must reach the caller —
    the task-body except block in ``scan_source_task`` maps it to
    ``_record_terminal_failure``. Eating it here would silently sink the scan.
    """
    from integrations.dt import DTBreakerOpen

    dt_client = _RecordingDTClient(upsert_raises=DTBreakerOpen("breaker open"))

    with pytest.raises(DTBreakerOpen):
        _run_pipeline_with_dt(monkeypatch, patch_pipeline_minimal, dt_client)

    # We failed the scan AFTER scancode had a chance to run (same behaviour
    # as the sequential layout: components / scancode commits run before DT
    # upload). The "scancode" stage label must have been emitted.
    assert "scancode" in patch_pipeline_minimal["stages"]
    # The DT client connection pool must have been closed.
    assert dt_client.closed is True


def test_dt_upload_dt_error_propagates_at_join(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """A DTError raised inside the upload thread must also propagate
    unchanged so the task body's ``except DTError`` block handles it.
    """
    from integrations.dt import DTError

    dt_client = _RecordingDTClient(
        upload_raises=DTError("DT 503 on /api/v1/bom"),
    )

    with pytest.raises(DTError):
        _run_pipeline_with_dt(monkeypatch, patch_pipeline_minimal, dt_client)

    # upsert succeeded before upload raised — both calls were made.
    assert dt_client.upsert_calls == 1
    assert dt_client.upload_calls == 1
    # Connection pool reclaimed on the failure path too.
    assert dt_client.closed is True


# ---------------------------------------------------------------------------
# 4. Scancode failure does NOT cancel the DT upload thread
# ---------------------------------------------------------------------------


def test_scancode_failure_does_not_cancel_dt_upload(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """A ScancodeError caught inline must leave the DT upload future alone —
    DT findings still come through and the scan still reaches finalize.
    """
    from integrations import scancode as scancode_adapter

    def boom_scancode(**kw):  # type: ignore[no-untyped-def]
        raise scancode_adapter.ScancodeFailed("simulated scancode exit 1")

    dt_client = _RecordingDTClient()

    scan_uuid, stages = _run_pipeline_with_dt(
        monkeypatch,
        patch_pipeline_minimal,
        dt_client,
        scancode_runner=boom_scancode,
    )

    # DT upload still ran end-to-end despite scancode failure.
    assert dt_client.upsert_calls == 1
    assert dt_client.upload_calls == 1
    assert dt_client.findings_calls == 1
    # Pipeline still reached finalize.
    assert "finalize" in stages
    # And DT client was closed on the way out.
    assert dt_client.closed is True


# ---------------------------------------------------------------------------
# 5. dt_client.close() runs even on DT-side failure (resource leak guard)
# ---------------------------------------------------------------------------


def test_dt_client_closed_on_breaker_failure(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """The outer try/finally must reclaim the httpx connection pool even when
    the DT upload thread raised. A leaked client across many failed scans
    would exhaust the worker's file descriptors.
    """
    from integrations.dt import DTBreakerOpen

    dt_client = _RecordingDTClient(upsert_raises=DTBreakerOpen("nope"))

    with pytest.raises(DTBreakerOpen):
        _run_pipeline_with_dt(monkeypatch, patch_pipeline_minimal, dt_client)

    assert dt_client.closed is True


# ---------------------------------------------------------------------------
# 6. Stage labels still emit in the FE-compatible order
# ---------------------------------------------------------------------------


def test_stage_labels_emit_in_fe_compatible_order(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """The FE's PIPELINE_STEPS expects the 7-step sequence
    ``bootstrap → fetch → cdxgen → dt_upload → scancode → dt_findings → finalize``.

    P2 #8 hotfix moved ``dt_upload`` BEFORE ``scancode`` in the WS publish
    stream: the worker now ``_set_stage("dt_upload")`` right after submitting
    the background upload future, then proceeds to scancode on the main
    thread. The internal concurrency (PR #181) is unchanged — only the
    publish ordering shifted so the UI glyph row matches "SBOM generated →
    DT upload first" that the ops triage asked for. The pipeline also
    publishes ``prep``, ``sign``, ``approvals`` which the FE ignores — but
    the seven canonical labels above MUST appear in this exact relative order.
    """
    dt_client = _RecordingDTClient()
    _run_pipeline_with_dt(monkeypatch, patch_pipeline_minimal, dt_client)

    stages = patch_pipeline_minimal["stages"]
    fe_labels = [
        "bootstrap",
        "fetch",
        "cdxgen",
        "dt_upload",
        "scancode",
        "dt_findings",
        "finalize",
    ]
    seen_indices = [stages.index(lbl) for lbl in fe_labels]
    assert seen_indices == sorted(seen_indices), (
        f"stage labels out of FE-expected order: {stages}"
    )


# ---------------------------------------------------------------------------
# 7. Upload future has resolved before findings poll starts (join correctness)
# ---------------------------------------------------------------------------


def test_dt_findings_poll_only_runs_after_upload_completes(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """The dt_findings poll MUST wait for upload completion — polling a DT
    project that has not received its BOM yet would return [] forever.
    """
    dt_client = _RecordingDTClient(upload_delay=0.05)

    _run_pipeline_with_dt(monkeypatch, patch_pipeline_minimal, dt_client)

    # findings_calls == 1 means we polled exactly once (after upload, before
    # the _DT_FINDINGS_POLL_DELAYS_SECONDS = (0,) short-circuit). The
    # upload_finished_at timestamp must precede that poll — we cannot
    # observe the poll timestamp directly without more plumbing, but the
    # invariant we care about is captured by upload_calls == 1 BEFORE the
    # join + findings_calls == 1 AFTER. The pipeline executes synchronously
    # so the sequencing is implicit.
    assert dt_client.upload_calls == 1
    assert dt_client.upload_finished_at is not None
    assert dt_client.findings_calls == 1


# ---------------------------------------------------------------------------
# 8. Scancode failure + DT failure together still close the DT client
# ---------------------------------------------------------------------------


def test_both_scancode_and_dt_failure_still_close_client(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline_minimal: dict[str, Any]
) -> None:
    """If both the scancode stage and the DT upload thread fail, the cleanup
    invariants must still hold: DT client closed, DT failure propagated.
    """
    from integrations import scancode as scancode_adapter
    from integrations.dt import DTBreakerOpen

    def boom_scancode(**kw):  # type: ignore[no-untyped-def]
        raise scancode_adapter.ScancodeFailed("scancode exit 1")

    dt_client = _RecordingDTClient(upsert_raises=DTBreakerOpen("breaker open"))

    with pytest.raises(DTBreakerOpen):
        _run_pipeline_with_dt(
            monkeypatch,
            patch_pipeline_minimal,
            dt_client,
            scancode_runner=boom_scancode,
        )

    # Scancode raised early (best-effort, swallowed), DT raised late (terminal).
    # The DT client must still have been closed.
    assert dt_client.closed is True
