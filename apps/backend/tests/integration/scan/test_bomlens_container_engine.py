"""SCAN_CONTAINER_ENGINE=bomlens end to end: the task, the runner, the adapter, the scan row.

The engine container is played by :class:`tests._bomlens_engine.FakeDocker`,
whose effect copies a real ``MODE=IMAGE`` run of the pinned BomLens image
(``tests/fixtures/bomlens/image-registry-alpine``, see its ``PROVENANCE.md``)
into the folder the runner mounted. Everything after the container is real:
the adapter writes through the persist paths into the database, and each test
reads the rows back in a separate session.

The last test runs the published image on a real Docker daemon. It is local
only (``BOMLENS_ENGINE_DOCKER_TEST=1``), like the source engine's.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from integrations.bomlens import runner as runner_mod
from models import (
    ComponentDependencyEdge,
    License,
    LicenseFinding,
    Scan,
    ScanArtifact,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from tests._bomlens_engine import FIXTURES, Behaviour, FakeDocker, captured_images, write_result

pytestmark = pytest.mark.integration

#: The image the fixture scanned (PROVENANCE.md), pinned by its index digest.
ALPINE = (
    "alpine:3.19.0@sha256:51b67269f354137895d43f3b3d810bfacd3945438e94dc5ac55fdac340352f48"
)
CASE = "image-registry-alpine"
#: A proxy carrying credentials, as an operator may configure one.
PROXY_SECRET = "proxy-secret-4f1c9"
PROXY = f"http://proxyuser:{PROXY_SECRET}@proxy.example:3128"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    from tests._db_required import migrate_to_head

    migrate_to_head()


@pytest.fixture(autouse=True)
def _engine_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    trivy = tmp_path / "trivy" / "db"
    trivy.mkdir(parents=True)
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "false")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(root))
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(tmp_path / "trivy"))
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bind")
    monkeypatch.setenv("SCAN_BOMLENS_IMAGE", captured_images())
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", "bridge")
    monkeypatch.setenv("SCAN_CONTAINER_ENGINE", "bomlens")
    monkeypatch.setenv("TRUSCA_INSTANCE_ID", "container-tests")
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    return root


def _install(monkeypatch: pytest.MonkeyPatch, docker: FakeDocker) -> None:
    monkeypatch.setattr(runner_mod, "DockerCli", lambda: docker)
    monkeypatch.setattr(
        "tasks.scan_container_bomlens.shutil.which", lambda _name: "/usr/bin/docker"
    )


def _clean_docker() -> FakeDocker:
    return FakeDocker().behave("image", Behaviour(effect=write_result(CASE)))


def _seed(
    *,
    image_ref: str = ALPINE,
    status: str = "queued",
    credential_host: str | None = None,
    credential_elsewhere: bool = False,
) -> uuid.UUID:
    """A container scan row; optionally a stored registry credential for its
    organization (``credential_host``) or for another organization."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from models import RegistryCredential
    from tests._helpers import (
        make_membership,
        make_organization,
        make_project,
        make_team,
        make_user,
    )

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            owner = org
            if credential_elsewhere:
                owner = await make_organization(s)
            if credential_host is not None:
                s.add(
                    RegistryCredential(
                        organization_id=owner.id,
                        registry_host=credential_host,
                        username="registry-bot-7a2e",
                        # Never decrypted on the engine path (asserted below).
                        password_encrypted="stored-ciphertext-9d3b",
                    )
                )
            scan = Scan(
                project_id=project.id,
                kind="container",
                status=status,
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={"image_ref": image_ref},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def _set(scan_id: uuid.UUID, **values: Any) -> None:
    from core.db import sync_session_scope

    with sync_session_scope() as session:
        scan = session.get(Scan, scan_id)
        assert scan is not None
        for key, value in values.items():
            setattr(scan, key, value)
        session.commit()


def _scan(session: Session, scan_id: uuid.UUID) -> Scan:
    session.expire_all()
    scan = session.get(Scan, scan_id)
    assert scan is not None
    return scan


def _count(session: Session, model: Any, scan_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(model).where(model.scan_id == scan_id)
        ).scalar_one()
    )


def _cve_ids(session: Session, scan_id: uuid.UUID) -> set[str]:
    rows = session.execute(
        select(Vulnerability.external_id)
        .join(VulnerabilityFinding, VulnerabilityFinding.vulnerability_id == Vulnerability.id)
        .where(VulnerabilityFinding.scan_id == scan_id)
    ).scalars()
    return set(rows)


def _run_task(scan_id: uuid.UUID) -> None:
    from tasks.scan_container import scan_container_engine_task

    result = scan_container_engine_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback


def _fixture_report() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        (FIXTURES / CASE / "image_1.0.0_security.json").read_text(encoding="utf-8")
    )
    return document


# ---------------------------------------------------------------------------
# The scan, persisted
# ---------------------------------------------------------------------------


def test_the_engine_task_scans_the_image_and_persists_what_the_engine_found(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session, _engine_env: Path
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed()

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded", scan.error_message
    assert (scan.progress_percent, scan.current_step) == (100, "finalize")
    assert [r.label for r in docker.runs] == ["image"]

    # The inventory: the 15 packages, not the 80 file entries or the OS.
    assert _count(sync_session, ScanComponent, scan_id) == 15
    # The vulnerabilities: every CVE the engine's Trivy report lists.
    report = _fixture_report()
    expected = {
        v["VulnerabilityID"] for r in report["Results"] for v in r.get("Vulnerabilities") or []
    }
    assert len(expected) == 21
    assert _cve_ids(sync_session, scan_id) == expected
    assert _count(sync_session, VulnerabilityFinding, scan_id) == 48
    # Declared licences and the dependency edges between the packages.
    spdx = set(
        sync_session.execute(
            select(License.spdx_id)
            .join(LicenseFinding, LicenseFinding.license_id == License.id)
            .where(LicenseFinding.scan_id == scan_id)
        ).scalars()
    )
    assert {"GPL-2.0-only", "MIT", "Apache-2.0", "Zlib"} <= spdx
    assert _count(sync_session, ComponentDependencyEdge, scan_id) > 0

    meta = dict(scan.scan_metadata or {})
    # The image OS from the engine's report, as the in-process path records it.
    assert meta["os"] == {"family": "alpine", "name": "3.19.0", "eosl": True}
    assert meta["scan_engine"]["input_kind"] == "image"
    assert meta["scan_engine"]["run_status"] == "succeeded"
    run = meta["scan_engine_run"]
    assert run["mode"] == "image"
    assert run["image_target"] == f"registry:{ALPINE}"
    assert run["exit_code"] == 0
    assert run["engine_image"] == captured_images()
    kinds = set(
        sync_session.execute(
            select(ScanArtifact.kind).where(ScanArtifact.scan_id == scan_id)
        ).scalars()
    )
    assert kinds == {"sbom_cyclonedx", "trivy_sbom_report"}

    # The container is gone, and the workspace with it.
    assert sorted(docker.removed) == sorted(docker.containers)
    assert not (_engine_env / str(scan_id)).exists()


def test_the_engine_container_gets_the_hardened_run_and_nothing_more(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed()

    _run_task(scan_id)

    (run,) = docker.runs_of("image")
    env = run.env
    assert env["MODE"] == "IMAGE"
    # Handed over with the registry scheme: syft reads a registry and nothing else.
    assert env["TARGET_IMAGE"] == f"registry:{ALPINE}"
    assert env["GENERATE_SECURITY"] == "true"
    assert env["TRIVY_CACHE_DIR"] == "/trivy-db"
    assert env["TRIVY_SKIP_DB_UPDATE"] == "true"
    from integrations.bomlens.runner import trivy_db_max_age_days

    assert env["BOMLENS_TRIVY_DB_MAX_AGE_DAYS"] == str(trivy_db_max_age_days())
    # The image TRUSCA pins, by digest.
    assert run.image == captured_images()
    # Non-root (the worker's own user), read-only root, every capability dropped.
    (user,) = run.option_values("--user")
    assert user == f"{os.getuid()}:{os.getgid()}"
    assert not user.startswith("0:")
    assert run.has_flag("--read-only")
    assert run.option_values("--cap-add") == []
    assert "ALL" in run.option_values("--cap-drop")
    assert run.option_values("--security-opt") == ["no-new-privileges"]
    assert run.option_values("--network") == ["bridge"]
    # Exactly three mounts: output and scratch from the scan's workspace, and
    # the worker's Trivy database read-only. No Docker socket.
    assert set(run.mounts) == {"/host-output", "/tmp", "/trivy-db"}
    assert run.mounts["/trivy-db"].get("readonly") == "true"
    workspace = Path(os.environ["WORKSPACE_HOST_PATH"]).resolve() / str(scan_id)
    for target in ("/host-output", "/tmp"):
        assert Path(run.mounts[target]["src"]).is_relative_to(workspace), target
    assert not any("docker.sock" in str(m) for m in run.mounts.values())
    assert "DOCKER_HOST" not in env
    # The labels the reaper reads.
    labels = run.option_values("--label")
    assert "trusca.role=bomlens-engine" in labels
    assert f"trusca.scan={scan_id.hex}" in labels
    assert "trusca.instance=container-tests" in labels
    assert any(label.startswith("trusca.deadline=") for label in labels)


def test_progress_reports_the_container_stages_in_order(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    published: list[tuple[str, int]] = []

    def record(scan_uuid: Any, *, step: str, percent: int) -> None:  # noqa: ARG001
        published.append((step, percent))

    monkeypatch.setattr("tasks.scan_container.publish_progress", record)
    monkeypatch.setattr("tasks._scan_pipeline.publish_progress", record)
    _install(monkeypatch, _clean_docker())
    scan_id = _seed()

    _run_task(scan_id)

    assert [s for s, _ in published] == ["bootstrap", "trivy", "persist", "finalize", "succeeded"]
    percents = [p for _, p in published]
    assert percents == sorted(percents), published
    assert percents[-1] == 100


def test_a_redelivered_running_scan_starts_over_without_duplicates(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """Celery redelivers a running scan after the worker died: the second run
    replaces the first one's rows rather than adding to them."""
    _install(monkeypatch, _clean_docker())
    scan_id = _seed()
    _run_task(scan_id)
    first = (
        _count(sync_session, ScanComponent, scan_id),
        _count(sync_session, VulnerabilityFinding, scan_id),
        _count(sync_session, LicenseFinding, scan_id),
        _count(sync_session, ComponentDependencyEdge, scan_id),
    )

    _set(scan_id, status="running")
    _run_task(scan_id)

    assert _scan(sync_session, scan_id).status == "succeeded"
    second = (
        _count(sync_session, ScanComponent, scan_id),
        _count(sync_session, VulnerabilityFinding, scan_id),
        _count(sync_session, LicenseFinding, scan_id),
        _count(sync_session, ComponentDependencyEdge, scan_id),
    )
    assert second == first
    assert first[0] == 15


# ---------------------------------------------------------------------------
# What is refused before any container starts
# ---------------------------------------------------------------------------


def test_the_registry_allowlist_is_enforced_on_the_engine_path(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "ghcr.io")
    scan_id = _seed(image_ref="evil.example.com/ghcr.io/app:1")

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    assert "evil.example.com" in (scan.error_message or "")
    assert "CONTAINER_SCAN_ALLOWED_REGISTRIES" in (scan.error_message or "")
    assert docker.runs == []


def test_an_allowed_registry_still_runs(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "docker.io")
    scan_id = _seed()

    _run_task(scan_id)

    assert _scan(sync_session, scan_id).status == "succeeded"
    assert docker.runs_of("image")


@pytest.mark.parametrize(
    "image_ref",
    [
        "docker-archive:/etc/passwd",
        "oci-dir:/trivy-db",
        "https://registry.example/app:1",
        "registry-user:s3cr3t-token@registry.example/app:1",
        "registry.example/App:1",
        "alpine:3.19 --privileged",
        "-alpine",
    ],
)
def test_a_reference_that_is_not_a_registry_image_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    image_ref: str,
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed(image_ref=image_ref)

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    message = scan.error_message or ""
    assert message.startswith("scan engine: the image reference is not a registry image")
    assert docker.runs == []
    # The value is in neither the scan's error nor the worker's log.
    logged = capsys.readouterr()
    for text in (message, logged.out, logged.err, caplog.text):
        assert image_ref not in text
        assert "s3cr3t-token" not in text


def test_a_registry_with_a_stored_credential_is_refused_without_reading_it(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The engine does not pass registry credentials, so it refuses an image
    whose registry has one rather than pulling anonymously and failing on
    the way, or worse, scanning a different public image of the same name."""
    import services.registry_credential_service as credentials

    decrypted: list[str] = []
    monkeypatch.setattr(credentials, "decrypt_secret", lambda token, **_k: decrypted.append(token))
    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed(credential_host="docker.io")

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    message = scan.error_message or ""
    assert "registry 'docker.io' has a stored registry credential" in message
    assert "SCAN_CONTAINER_ENGINE=inprocess" in message
    assert docker.runs == []
    assert decrypted == []
    logged = capsys.readouterr()
    stored = json.dumps(scan.scan_metadata, default=str)
    for text in (message, stored, logged.out, logged.err, caplog.text):
        assert "registry-bot-7a2e" not in text
        assert "stored-ciphertext-9d3b" not in text


@pytest.mark.parametrize(
    ("host", "elsewhere"),
    [("ghcr.io", False), ("docker.io", True)],
    ids=["other-registry", "other-organization"],
)
def test_a_credential_for_another_registry_or_organization_does_not_block(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session, host: str, elsewhere: bool
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed(credential_host=host, credential_elsewhere=elsewhere)

    _run_task(scan_id)

    assert _scan(sync_session, scan_id).status == "succeeded"
    (run,) = docker.runs_of("image")
    login_names = ("SYFT_REGISTRY", "TRIVY_USERNAME", "TRIVY_PASSWORD")
    assert not any(name.startswith(login_names) for name in run.env)
    assert "DOCKER_CONFIG" not in run.env


def test_a_proxy_with_credentials_reaches_the_container_by_name_only(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one secret the container can get: a proxy URL. Its value reaches
    the docker CLI through its own environment, never a command-line
    argument, the scan metadata or the log."""
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    monkeypatch.setenv("NO_PROXY", "localhost")
    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed()

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded", scan.error_message
    (run,) = docker.runs_of("image")
    assert run.env["HTTPS_PROXY"] == PROXY
    assert "HTTPS_PROXY" in run.by_name
    assert not any(PROXY_SECRET in arg for arg in run.argv)
    assert docker.process_envs[0]["HTTPS_PROXY"] == PROXY
    logged = capsys.readouterr()
    stored = json.dumps(scan.scan_metadata, default=str)
    for text in (stored, scan.error_message or "", logged.out, logged.err, caplog.text):
        assert PROXY_SECRET not in text


def test_no_egress_network_configured_refuses_before_any_container(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.delenv("SCAN_BOMLENS_NETWORK")
    scan_id = _seed()

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    assert "SCAN_BOMLENS_NETWORK is not set" in (scan.error_message or "")
    assert docker.runs == []


@pytest.mark.parametrize("network", ["trustedoss", "0f3c9a2b7d1e", "host"])
def test_the_worker_s_own_network_is_refused(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session, network: str
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", network)
    scan_id = _seed()

    _run_task(scan_id)

    assert _scan(sync_session, scan_id).status == "failed"
    assert docker.runs == []


def test_a_worker_running_as_root_does_not_lend_root_to_the_container(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setattr(
        runner_mod.BomLensRunner,
        "_owner_env",
        lambda self: [("HOST_UID", "0"), ("HOST_GID", "0")],
    )
    scan_id = _seed()

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    assert "non-root" in (scan.error_message or "")
    assert docker.runs == []


# ---------------------------------------------------------------------------
# Failures of the engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("behaviour", "fragment"),
    [
        (
            Behaviour(exit_code=1, effect=write_result("source-run-failed")),
            "the BomLens run failed",
        ),
        (
            Behaviour(effect=write_result("source-vulnerabilities-failed")),
            "vulnerability scan failed",
        ),
        (Behaviour(exit_code=1), "BomLens wrote no result manifest"),
        (Behaviour(hang=True), "stopped at its time limit"),
        (Behaviour(stuck=True), "still running after it was killed"),
    ],
    ids=["run-failed", "vulnerabilities-failed", "no-manifest", "timed-out", "will-not-stop"],
)
def test_an_engine_failure_fails_the_scan_with_a_message_and_no_path(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
    _engine_env: Path,
    behaviour: Behaviour,
    fragment: str,
) -> None:
    docker = FakeDocker().behave("image", behaviour)
    _install(monkeypatch, docker)
    scan_id = _seed()

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    message = scan.error_message or ""
    assert message.startswith("scan engine: "), message
    assert fragment in message
    assert str(_engine_env) not in message
    assert _count(sync_session, VulnerabilityFinding, scan_id) == 0
    # The container was removed on the failure path too.
    assert sorted(docker.removed) == sorted(docker.containers)


def test_a_failed_scan_is_not_restarted_and_a_new_scan_succeeds(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """Lifecycle: fail, then retry. A retry is a new queued scan (every
    enqueue path creates one); the failed row keeps its ending even when its
    task message arrives again."""
    failing = FakeDocker().behave("image", Behaviour(exit_code=1))
    _install(monkeypatch, failing)
    failed_id = _seed()
    _run_task(failed_id)
    first = _scan(sync_session, failed_id)
    assert first.status == "failed"
    failed_message = first.error_message

    docker = _clean_docker()
    _install(monkeypatch, docker)
    _run_task(failed_id)
    again = _scan(sync_session, failed_id)
    assert (again.status, again.error_message) == ("failed", failed_message)
    assert docker.runs == []

    retry_id = _seed()
    _run_task(retry_id)
    assert _scan(sync_session, retry_id).status == "succeeded"
    assert _count(sync_session, ScanComponent, retry_id) == 15


# ---------------------------------------------------------------------------
# Cancellation (N-M2, N-L4) and the reaper
# ---------------------------------------------------------------------------


def test_a_scan_cancelled_while_the_container_ran_is_left_cancelled(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    scan_id = _seed()

    def cancel_during_run(run: Any) -> None:
        write_result(CASE)(run)
        _set(scan_id, status="cancelled", error_message="cancelled by user")

    docker = FakeDocker().behave("image", Behaviour(effect=cancel_during_run))
    _install(monkeypatch, docker)

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert (scan.status, scan.error_message) == ("cancelled", "cancelled by user")
    assert _count(sync_session, ScanComponent, scan_id) == 0
    assert _count(sync_session, VulnerabilityFinding, scan_id) == 0


@pytest.mark.parametrize(
    ("behaviour", "writer_reached"),
    [
        # The reaper removed the container (exit 137, no manifest): the status
        # check after the run sees the cancellation first.
        (Behaviour(exit_code=137), False),
        # The container would not stop: the runner raises before that check,
        # so only the conditional failure writer keeps the cancellation.
        (Behaviour(stuck=True), True),
    ],
    ids=["removed", "will-not-stop"],
)
def test_an_engine_error_after_a_cancel_does_not_overwrite_it(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
    behaviour: Behaviour,
    writer_reached: bool,
) -> None:
    from tasks._scan_pipeline import mark_failed as real

    scan_id = _seed()

    def cancel(run: Any) -> None:
        del run
        _set(scan_id, status="cancelled", error_message="cancelled by admin")

    behaviour.effect = cancel
    docker = FakeDocker().behave("image", behaviour)
    _install(monkeypatch, docker)
    calls: list[str] = []

    def spy(session: Any, scan: Any, message: str) -> None:
        calls.append(message)
        real(session, scan, message)

    monkeypatch.setattr("tasks.scan_container.mark_failed", spy)

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert (scan.status, scan.error_message) == ("cancelled", "cancelled by admin")
    assert bool(calls) is writer_reached, calls


@pytest.mark.parametrize("task_name", ["scan_container_engine_task", "scan_container_task"])
@pytest.mark.parametrize("ended", ["cancelled", "failed", "succeeded"])
def test_a_scan_that_ended_while_queued_is_not_run(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session, task_name: str, ended: str
) -> None:
    """Cancelled while queued: worker-engine never got the revoke, so the task
    arrives anyway. Neither container task restarts it, and no engine
    container or Trivy run starts."""
    import tasks.scan_container as scan_container

    docker = _clean_docker()
    _install(monkeypatch, docker)
    trivy_runs: list[str] = []
    monkeypatch.setattr(
        "tasks.scan_container.trivy_adapter.run_trivy_image",
        lambda **kw: trivy_runs.append(kw["image_ref"]),
    )
    scan_id = _seed()
    _set(scan_id, status=ended, error_message="ended before the task ran")

    result = getattr(scan_container, task_name).apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    scan = _scan(sync_session, scan_id)
    assert (scan.status, scan.error_message) == (ended, "ended before the task ran")
    assert docker.runs == []
    assert trivy_runs == []


def test_the_reaper_removes_the_container_of_a_cancelled_container_scan(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """The labels the image container is created with are what the reaper
    reads: running keeps it, cancelled removes it."""
    from integrations.bomlens.runner import EngineContainer
    from tasks.bomlens_engine_reaper import reap_engine_containers

    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed()
    _run_task(scan_id)
    (run,) = docker.runs_of("image")
    labels = dict(label.split("=", 1) for label in run.option_values("--label"))
    container = EngineContainer(
        id="left-behind",
        scan_hex=labels["trusca.scan"],
        deadline=int(labels["trusca.deadline"]),
        instance=labels["trusca.instance"],
    )
    reaper = FakeDocker()
    reaper.engine_list = [container]

    _set(scan_id, status="running")
    assert reap_engine_containers(reaper)["removed"] == 0
    _set(scan_id, status="cancelled")
    assert reap_engine_containers(reaper)["removed"] == 1
    assert reaper.removed == ["left-behind"]


# ---------------------------------------------------------------------------
# The real image on a real Docker daemon (local only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.getenv("BOMLENS_ENGINE_DOCKER_TEST") != "1",
    reason="runs the published BomLens image on a local Docker daemon "
    "(set BOMLENS_ENGINE_DOCKER_TEST=1 and BOMLENS_ENGINE_DOCKER_DIR to a folder the daemon sees)",
)
def test_the_published_image_scans_a_registry_image_without_a_docker_socket(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    host_dir = Path(os.environ["BOMLENS_ENGINE_DOCKER_DIR"]) / uuid.uuid4().hex
    host_dir.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(host_dir))
    monkeypatch.setenv(
        "TRIVY_CACHE_DIR",
        os.getenv("BOMLENS_ENGINE_TRIVY_CACHE", str(Path.home() / ".cache" / "trivy")),
    )
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE")
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", os.getenv("BOMLENS_ENGINE_NETWORK", "bridge"))
    # This test process is not a container, so it has no networks of its own
    # for the runner to compare; in compose it is worker-engine and does.
    monkeypatch.setattr(runner_mod.DockerCli, "own_networks", lambda self: frozenset())
    scan_id = _seed()

    _run_task(scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded", scan.error_message
    assert _count(sync_session, ScanComponent, scan_id) == 15
    assert "CVE-2024-0727" in _cve_ids(sync_session, scan_id)
    meta = dict(scan.scan_metadata or {})
    assert meta["scan_engine"]["run_status"] == "succeeded"
    assert meta["scan_engine"]["input_kind"] == "image"
    assert meta["scan_engine_run"]["exit_code"] == 0
    assert meta["os"]["family"] == "alpine"
    # Nothing of the scan's workspace is left (the container ran as this user).
    assert not (host_dir / str(scan_id)).exists()
