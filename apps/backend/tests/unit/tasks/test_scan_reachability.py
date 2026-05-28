"""Unit tests for the v2.3 r1 reachability task (``tasks.scan_reachability``).

Two layers:

  1. Pure-unit (no DB, no subprocess):
       - ``_lookup_verdict`` id matching / case-normalisation.
       - ``_find_go_module_dir`` root vs nested vs vendored vs none.
       - ``_safe_extract_tarball`` clean extract + traversal / symlink rejection.
       - ``_run`` best-effort skips (missing scan, non-source, no preserved
         source) — patched so no DB / binary is touched.

  2. DB-backed (``integration`` marker, skipped when DATABASE_URL unset):
       - ``_apply_verdicts`` maps verdicts onto Go findings only, leaves
         non-Go / unmatched findings NULL, and is idempotent on re-run.

subprocess + govulncheck are always mocked; the real binary is never invoked.
DB tests run against a FRESH test DB (DATABASE_URL only) per the r1 brief.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from integrations import govulncheck as gv
from tasks import scan_reachability as sr

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Pure-unit: _lookup_verdict
# ---------------------------------------------------------------------------


def test_lookup_verdict_matches_case_insensitive() -> None:
    verdicts = {"CVE-2023-1111": True, "GHSA-AAAA-BBBB-CCCC": False}
    assert sr._lookup_verdict("cve-2023-1111", verdicts) is True
    assert sr._lookup_verdict("CVE-2023-1111", verdicts) is True
    assert sr._lookup_verdict("ghsa-aaaa-bbbb-cccc", verdicts) is False


def test_lookup_verdict_no_match_returns_none() -> None:
    assert sr._lookup_verdict("CVE-2023-9999", {"CVE-2023-1111": True}) is None
    assert sr._lookup_verdict(None, {"CVE-2023-1111": True}) is None
    assert sr._lookup_verdict(12345, {"CVE-2023-1111": True}) is None


# ---------------------------------------------------------------------------
# Pure-unit: _find_go_module_dir
# ---------------------------------------------------------------------------


def test_find_go_module_root(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    assert sr._find_go_module_dir(tmp_path) == tmp_path


def test_find_go_module_nested(tmp_path: Path) -> None:
    sub = tmp_path / "svc"
    sub.mkdir()
    (sub / "go.mod").write_text("module x\n", encoding="utf-8")
    assert sr._find_go_module_dir(tmp_path) == sub


def test_find_go_module_skips_vendor(tmp_path: Path) -> None:
    vend = tmp_path / "vendor" / "dep"
    vend.mkdir(parents=True)
    (vend / "go.mod").write_text("module dep\n", encoding="utf-8")
    assert sr._find_go_module_dir(tmp_path) is None


def test_find_go_module_none_when_not_go(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert sr._find_go_module_dir(tmp_path) is None


# ---------------------------------------------------------------------------
# Pure-unit: _safe_extract_tarball
# ---------------------------------------------------------------------------


def _make_tarball(path: Path, files: dict[str, str]) -> None:
    src = path.parent / "tar-src"
    src.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        f = src / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    with tarfile.open(path, mode="w:gz") as tar:
        for name in files:
            tar.add(str(src / name), arcname=name)


def test_safe_extract_clean(tmp_path: Path) -> None:
    tarball = tmp_path / "src.tar.gz"
    _make_tarball(tarball, {"go.mod": "module x\n", "main.go": "package main\n"})
    target = tmp_path / "out"

    ok = sr._safe_extract_tarball(tarball=tarball, target_dir=target)

    assert ok is True
    assert (target / "go.mod").is_file()
    assert (target / "main.go").read_text(encoding="utf-8") == "package main\n"


def test_safe_extract_rejects_traversal(tmp_path: Path) -> None:
    tarball = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "x").write_text("pwned", encoding="utf-8")
    with tarfile.open(tarball, mode="w:gz") as tar:
        tar.add(str(payload / "x"), arcname="../escape.txt")
    target = tmp_path / "out"

    ok = sr._safe_extract_tarball(tarball=tarball, target_dir=target)

    assert ok is False
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_skips_symlink_member(tmp_path: Path) -> None:
    tarball = tmp_path / "link.tar.gz"
    with tarfile.open(tarball, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="evil-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
        # plus a real file so the extract still "succeeds"
        data = b"module x\n"
        finfo = tarfile.TarInfo(name="go.mod")
        finfo.size = len(data)
        import io

        tar.addfile(finfo, io.BytesIO(data))
    target = tmp_path / "out"

    ok = sr._safe_extract_tarball(tarball=tarball, target_dir=target)

    assert ok is True
    assert (target / "go.mod").is_file()
    assert not (target / "evil-link").exists()


def test_safe_extract_bad_tar_returns_false(tmp_path: Path) -> None:
    bad = tmp_path / "broken.tar.gz"
    bad.write_bytes(b"not a tarball at all")
    ok = sr._safe_extract_tarball(tarball=bad, target_dir=tmp_path / "out")
    assert ok is False


# ---------------------------------------------------------------------------
# Pure-unit: _run best-effort skips (DB + tarball patched)
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal sync-session stand-in for the read-only id snapshot in _run."""

    def __init__(self, objs: dict[tuple[type, uuid.UUID], object]) -> None:
        self._objs = objs

    def get(self, model: type, key: uuid.UUID) -> object | None:
        return self._objs.get((model, key))

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _patch_session(monkeypatch: pytest.MonkeyPatch, fake: _FakeSession) -> None:
    monkeypatch.setattr(
        "tasks.scan_reachability.sync_session_scope", lambda: fake
    )


def test_run_skips_missing_scan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_session(monkeypatch, _FakeSession({}))
    called = {"gv": False}
    monkeypatch.setattr(
        gv, "run_govulncheck", lambda **_k: called.__setitem__("gv", True)
    )
    sr._run(scan_uuid=uuid.uuid4(), workspace=tmp_path / "ws")
    assert called["gv"] is False


def test_run_skips_non_source_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from models import Scan

    scan_id = uuid.uuid4()

    class _Scan:
        kind = "container"
        project_id = uuid.uuid4()

    _patch_session(monkeypatch, _FakeSession({(Scan, scan_id): _Scan()}))
    called = {"gv": False}
    monkeypatch.setattr(
        gv, "run_govulncheck", lambda **_k: called.__setitem__("gv", True)
    )
    sr._run(scan_uuid=scan_id, workspace=tmp_path / "ws")
    assert called["gv"] is False


def test_run_skips_when_no_preserved_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from models import Project, Scan

    scan_id = uuid.uuid4()
    proj_id = uuid.uuid4()

    class _Scan:
        kind = "source"
        project_id = proj_id

    class _Project:
        id = proj_id

    _patch_session(
        monkeypatch,
        _FakeSession({(Scan, scan_id): _Scan(), (Project, proj_id): _Project()}),
    )
    # Point the tarball resolver at a non-existent path.
    monkeypatch.setattr(
        "tasks.scan_reachability.scan_source_tarball_path",
        lambda pid, sid: tmp_path / "nope" / f"{sid}.tar.gz",
    )
    called = {"gv": False}
    monkeypatch.setattr(
        gv, "run_govulncheck", lambda **_k: called.__setitem__("gv", True)
    )

    sr._run(scan_uuid=scan_id, workspace=tmp_path / "ws")

    assert called["gv"] is False


def test_task_entrypoint_cleans_workspace_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Celery entry point removes its workspace in finally even on error."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))

    scan_id = uuid.uuid4()

    def _boom(*, scan_uuid: uuid.UUID, workspace: Path) -> None:
        # Materialise the workspace tree (as the real _run does) BEFORE blowing
        # up, so the assertion proves the task's `finally` actually reclaims it.
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "leak.txt").write_text("x", encoding="utf-8")
        raise RuntimeError("kaboom")

    monkeypatch.setattr("tasks.scan_reachability._run", _boom)

    # Run eagerly through Celery so the task gets a real request context. The
    # task's broad except swallows the error (best-effort), and the `finally`
    # must still have removed the workspace tree.
    sr.scan_reachability_task.apply(args=[str(scan_id)])

    assert not (tmp_path / f"reach-{scan_id}").exists()


# ---------------------------------------------------------------------------
# Pure-unit: _run full happy path (extract + module + gv + apply all patched)
# ---------------------------------------------------------------------------


def test_run_full_path_invokes_govulncheck_and_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Go source scan with a preserved tarball runs gv and applies verdicts."""
    from models import Project, Scan

    scan_id = uuid.uuid4()
    proj_id = uuid.uuid4()

    class _Scan:
        kind = "source"
        project_id = proj_id

    class _Project:
        id = proj_id

    _patch_session(
        monkeypatch,
        _FakeSession({(Scan, scan_id): _Scan(), (Project, proj_id): _Project()}),
    )

    # A real preserved tarball that contains a go.mod at the root.
    tarball = tmp_path / f"{scan_id}.tar.gz"
    _make_tarball(tarball, {"go.mod": "module x\n", "main.go": "package main\n"})
    monkeypatch.setattr(
        "tasks.scan_reachability.scan_source_tarball_path",
        lambda pid, sid: tarball,
    )

    # govulncheck returns one reachable verdict.
    monkeypatch.setattr(
        gv,
        "run_govulncheck",
        lambda **_k: gv.ReachabilityResult(
            verdicts={"CVE-2023-1234": True}, analysed=True
        ),
    )
    applied: dict[str, object] = {}

    def _spy_apply(**kw: object) -> tuple[int, int]:
        applied.update(kw)
        return (1, 1)

    monkeypatch.setattr("tasks.scan_reachability._apply_verdicts", _spy_apply)

    sr._run(scan_uuid=scan_id, workspace=tmp_path / "ws")

    assert applied["scan_uuid"] == scan_id
    assert applied["verdicts"] == {"CVE-2023-1234": True}


def test_run_skips_apply_when_not_analysed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty/unanalysed govulncheck result writes nothing."""
    from models import Project, Scan

    scan_id = uuid.uuid4()
    proj_id = uuid.uuid4()

    class _Scan:
        kind = "source"
        project_id = proj_id

    class _Project:
        id = proj_id

    _patch_session(
        monkeypatch,
        _FakeSession({(Scan, scan_id): _Scan(), (Project, proj_id): _Project()}),
    )
    tarball = tmp_path / f"{scan_id}.tar.gz"
    _make_tarball(tarball, {"go.mod": "module x\n"})
    monkeypatch.setattr(
        "tasks.scan_reachability.scan_source_tarball_path", lambda pid, sid: tarball
    )
    monkeypatch.setattr(
        gv,
        "run_govulncheck",
        lambda **_k: gv.ReachabilityResult(verdicts={}, analysed=False),
    )
    applied = {"called": False}

    def _spy_apply(**_kw: object) -> tuple[int, int]:
        applied["called"] = True
        return (0, 0)

    monkeypatch.setattr("tasks.scan_reachability._apply_verdicts", _spy_apply)

    sr._run(scan_uuid=scan_id, workspace=tmp_path / "ws")

    assert applied["called"] is False


def test_run_skips_apply_when_no_go_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A preserved tree with no go.mod → not a Go project → gv never runs."""
    from models import Project, Scan

    scan_id = uuid.uuid4()
    proj_id = uuid.uuid4()

    class _Scan:
        kind = "source"
        project_id = proj_id

    class _Project:
        id = proj_id

    _patch_session(
        monkeypatch,
        _FakeSession({(Scan, scan_id): _Scan(), (Project, proj_id): _Project()}),
    )
    tarball = tmp_path / f"{scan_id}.tar.gz"
    _make_tarball(tarball, {"package.json": "{}\n"})
    monkeypatch.setattr(
        "tasks.scan_reachability.scan_source_tarball_path", lambda pid, sid: tarball
    )
    gv_called = {"v": False}
    monkeypatch.setattr(
        gv, "run_govulncheck", lambda **_k: gv_called.__setitem__("v", True)
    )

    sr._run(scan_uuid=scan_id, workspace=tmp_path / "ws")

    assert gv_called["v"] is False


# ---------------------------------------------------------------------------
# Pure-unit: extract guards (dir member, member-count cap, bomb cap)
# ---------------------------------------------------------------------------


def test_safe_extract_creates_dir_members(tmp_path: Path) -> None:
    tarball = tmp_path / "withdir.tar.gz"
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "f.go").write_text("package pkg\n", encoding="utf-8")
    with tarfile.open(tarball, mode="w:gz") as tar:
        tar.add(str(src / "pkg"), arcname="pkg")  # recursive: dir + file members
    target = tmp_path / "out"

    ok = sr._safe_extract_tarball(tarball=tarball, target_dir=target)

    assert ok is True
    assert (target / "pkg").is_dir()
    assert (target / "pkg" / "f.go").is_file()


def test_safe_extract_rejects_too_many_members(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sr, "_MAX_EXTRACT_MEMBERS", 1)
    tarball = tmp_path / "many.tar.gz"
    _make_tarball(tarball, {"a.txt": "1", "b.txt": "2", "c.txt": "3"})
    ok = sr._safe_extract_tarball(tarball=tarball, target_dir=tmp_path / "out")
    assert ok is False


def test_safe_extract_rejects_bomb(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sr, "_MAX_EXTRACT_BYTES", 4)
    tarball = tmp_path / "bomb.tar.gz"
    _make_tarball(tarball, {"big.txt": "x" * 1000})
    ok = sr._safe_extract_tarball(tarball=tarball, target_dir=tmp_path / "out")
    assert ok is False


def test_is_within_handles_value_error() -> None:
    # Identical path → True; unrelated path → False (no exception escapes).
    base = Path("/work/base")
    assert sr._is_within(base, base) is True
    assert sr._is_within(base, Path("/work/other")) is False


# ---------------------------------------------------------------------------
# DB-backed: _apply_verdicts mapping + idempotency
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.integration

_ALEMBIC_RAN = False


@pytest.fixture(scope="module")
def _migrate_once() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skip reachability DB tests")
    global _ALEMBIC_RAN
    if _ALEMBIC_RAN:
        return
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed:\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    _ALEMBIC_RAN = True


@pytest.fixture
def session(_migrate_once: None) -> Iterator[object]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


def _seed_finding(
    session: object,
    *,
    scan_id: uuid.UUID,
    purl: str,
    external_id: str,
) -> tuple[uuid.UUID, str]:
    """Seed component → version → vulnerability → finding.

    Returns ``(finding_id, namespaced_external_id)`` — the external id is
    suffixed for cross-suite isolation, so the caller builds its verdict map from
    the returned value rather than the bare input.
    """
    from models import (
        Component,
        ComponentVersion,
        Vulnerability,
        VulnerabilityFinding,
    )

    suffix = _suffix()
    comp = Component(
        purl=f"{purl}-{suffix}",
        package_type="golang" if purl.startswith("pkg:golang/") else "npm",
        name=f"name-{suffix}",
    )
    session.add(comp)  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    cv = ComponentVersion(
        component_id=comp.id,
        version="1.0.0",
        purl_with_version=f"{purl}-{suffix}@1.0.0",
    )
    session.add(cv)  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    namespaced_ext = f"{external_id}-{suffix}"
    vuln = Vulnerability(
        external_id=namespaced_ext,
        source="OSV",
        severity="high",
    )
    session.add(vuln)  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    finding = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv.id,
        vulnerability_id=vuln.id,
        status="new",
    )
    session.add(finding)  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    finding_id = finding.id
    session.commit()  # type: ignore[attr-defined]
    return finding_id, namespaced_ext


def _seed_scan(session: object) -> uuid.UUID:
    from models import Organization, Project, Scan, Team

    suffix = _suffix()
    org = Organization(name=f"Org {suffix}", slug=f"org-{suffix}")
    session.add(org)  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    team = Team(organization_id=org.id, name=f"Team {suffix}", slug=f"team-{suffix}")
    session.add(team)  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    project = Project(
        team_id=team.id, name=f"P {suffix}", slug=f"p-{suffix}", visibility="team"
    )
    session.add(project)  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    scan = Scan(
        project_id=project.id,
        kind="source",
        status="succeeded",
        progress_percent=100,
        scan_metadata={},
    )
    session.add(scan)  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    session.commit()  # type: ignore[attr-defined]
    return scan.id


@pytest.mark.integration
def test_apply_verdicts_marks_go_finding_and_leaves_others_null(
    session: object,
) -> None:
    from models import VulnerabilityFinding

    scan_id = _seed_scan(session)
    go_finding, go_ext = _seed_finding(
        session,
        scan_id=scan_id,
        purl="pkg:golang/github.com/vuln/pkg",
        external_id="CVE-2023-0001",
    )
    npm_finding, _ = _seed_finding(
        session,
        scan_id=scan_id,
        purl="pkg:npm/leftpad",
        external_id="CVE-2023-0002",
    )
    unmatched_go, _ = _seed_finding(
        session,
        scan_id=scan_id,
        purl="pkg:golang/github.com/other/pkg",
        external_id="CVE-2023-0003",
    )

    verdicts = {go_ext.upper(): True}

    updated, reachable = sr._apply_verdicts(scan_uuid=scan_id, verdicts=verdicts)

    assert updated == 1
    assert reachable == 1
    session.expire_all()  # type: ignore[attr-defined]
    go_row = session.get(VulnerabilityFinding, go_finding)  # type: ignore[attr-defined]
    assert go_row.reachable is True
    assert go_row.reachability_source == gv.SOURCE_LABEL
    assert go_row.reachability_analyzed_at is not None
    # npm finding: not Go → never touched.
    assert session.get(VulnerabilityFinding, npm_finding).reachable is None  # type: ignore[attr-defined,union-attr]
    # Go finding with no verdict → left NULL.
    assert session.get(VulnerabilityFinding, unmatched_go).reachable is None  # type: ignore[attr-defined,union-attr]


@pytest.mark.integration
def test_apply_verdicts_is_idempotent(session: object) -> None:
    from models import VulnerabilityFinding

    scan_id = _seed_scan(session)
    fid, ext = _seed_finding(
        session,
        scan_id=scan_id,
        purl="pkg:golang/github.com/vuln/pkg",
        external_id="CVE-2023-0010",
    )
    verdicts = {ext.upper(): False}

    u1, r1 = sr._apply_verdicts(scan_uuid=scan_id, verdicts=verdicts)
    u2, r2 = sr._apply_verdicts(scan_uuid=scan_id, verdicts=verdicts)

    assert (u1, r1) == (1, 0)
    assert (u2, r2) == (1, 0)  # same update, no new rows
    session.expire_all()  # type: ignore[attr-defined]
    row = session.get(VulnerabilityFinding, fid)  # type: ignore[attr-defined]
    assert row.reachable is False
    # Still exactly one finding row for this scan (no duplicate creation).
    from sqlalchemy import func, select

    count = session.execute(  # type: ignore[attr-defined]
        select(func.count())
        .select_from(VulnerabilityFinding)
        .where(VulnerabilityFinding.scan_id == scan_id)
    ).scalar_one()
    assert count == 1
