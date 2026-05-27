"""
Trivy adapter — defense-in-depth security guards (PR #196 follow-up).

This module pins the three security-reviewer Low findings on PR #196 that
were intentionally deferred from the W6-#40 core PR:

  L1 — Path traversal guard. ``run_trivy_image`` and ``run_trivy_sbom`` both
       reject ``output_dir`` / ``sbom_path`` that resolve outside
       ``WORKSPACE_HOST_PATH`` (covers literal ``..`` traversal and symlinks
       pointing outside the workspace). Today the only callers are Celery
       tasks that build paths from a trusted ``workspace_root() / <uuid>``
       prefix, but a future API caller or admin tool that surfaces external
       input would otherwise bypass workspace disk quotas, audit retention,
       and the workspace cleanup path.

  L2 — Output JSON size cap. ``_load_json`` refuses any file larger than
       256 MiB before the eager ``json.load`` could OOM the worker or
       deserialise into adversarial nested structures. Real Trivy output for
       even sprawling SBOMs sits in the low tens of MB.

  L4 — Error message path redaction. ``TrivyFailed`` and ``TrivyTimeout``
       carry a ``safe_detail`` attribute the caller surfaces in an RFC 7807
       ``problem+json`` ``detail`` field; the main message keeps the
       absolute path for ops logs. This prevents leaking workspace layout
       to API clients.

Per CLAUDE.md core rule #11, ``workspace_root()`` and ``scan_backend_mode()``
both resolve their env at call time. The integrations conftest pins
``WORKSPACE_HOST_PATH`` to the per-test ``tmp_path`` via an autouse fixture
so each test gets an isolated workspace boundary that matches its scratch
directory.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# L1 — Path traversal guard
# ---------------------------------------------------------------------------


def test_run_trivy_image_output_dir_inside_workspace_passes(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """The autouse conftest pins workspace_root = tmp_path; nested writes pass."""
    from integrations import trivy as trivy_adapter

    output_dir = tmp_path / "scan-uuid" / "trivy"
    result = trivy_adapter.run_trivy_image(
        image_ref="alpine:3.19",
        output_dir=output_dir,
    )
    assert result.report_path.exists()
    # The guard resolves the path so the report lives under tmp_path.
    assert result.report_path.resolve().is_relative_to(tmp_path.resolve())


def test_run_trivy_image_output_dir_outside_workspace_rejected(
    scan_backend_mock: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An output_dir that resolves outside WORKSPACE_HOST_PATH is refused.

    We hand-build an escape path that sits in a sibling of tmp_path so even
    after ``.resolve()`` follows any symlinks it lands outside the workspace
    root the conftest pinned.
    """
    from integrations import trivy as trivy_adapter

    # Construct a path that is unambiguously outside tmp_path even after
    # resolution: a sibling directory of tmp_path itself.
    escape = tmp_path.parent / "escape-target" / "trivy"

    with pytest.raises(ValueError) as excinfo:
        trivy_adapter.run_trivy_image(
            image_ref="alpine:3.19",
            output_dir=escape,
        )

    msg = str(excinfo.value)
    assert "output_dir" in msg
    assert "escapes workspace root" in msg
    # No file was written despite the rejection.
    assert not escape.exists()


def test_run_trivy_image_output_dir_with_dotdot_traversal_rejected(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """A path containing ``..`` that resolves outside the workspace is refused."""
    from integrations import trivy as trivy_adapter

    # tmp_path / subdir / .. / .. / escape — resolves to tmp_path.parent / escape,
    # which is outside the conftest-pinned workspace root.
    traversal = tmp_path / "subdir" / ".." / ".." / "escape"

    with pytest.raises(ValueError) as excinfo:
        trivy_adapter.run_trivy_image(
            image_ref="alpine:3.19",
            output_dir=traversal,
        )
    assert "escapes workspace root" in str(excinfo.value)


def test_run_trivy_sbom_output_dir_outside_workspace_rejected(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """``run_trivy_sbom`` guards ``output_dir`` the same way as ``run_trivy_image``."""
    from integrations import trivy as trivy_adapter

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")
    escape = tmp_path.parent / "escape-trivy"

    with pytest.raises(ValueError) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path,
            output_dir=escape,
        )
    assert "output_dir" in str(excinfo.value)
    assert not escape.exists()


def test_run_trivy_sbom_sbom_path_outside_workspace_rejected(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """``run_trivy_sbom`` also guards the ``sbom_path`` input.

    A caller that hands a path outside the workspace could otherwise point
    Trivy at an arbitrary file on the host (e.g. ``/etc/passwd``) and
    capture the parsing error stack trace via the failure log.
    """
    from integrations import trivy as trivy_adapter

    # Write a real SBOM file outside the workspace boundary.
    escape_sbom = tmp_path.parent / "outside-sbom.cdx.json"
    escape_sbom.write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(ValueError) as excinfo:
            trivy_adapter.run_trivy_sbom(
                sbom_path=escape_sbom,
                output_dir=tmp_path / "trivy",
            )
        assert "sbom_path" in str(excinfo.value)
        assert "escapes workspace root" in str(excinfo.value)
    finally:
        escape_sbom.unlink(missing_ok=True)


def test_run_trivy_sbom_symlink_pointing_outside_workspace_rejected(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """A symlink inside the workspace that points outside is followed and rejected.

    ``Path.resolve()`` follows symlinks, so a literal-looking-inside path
    cannot be used to smuggle the real Trivy write to a host location the
    caller doesn't own. This is the case the spec calls out specifically.
    """
    from integrations import trivy as trivy_adapter

    # Create a real directory outside the workspace, and a symlink inside
    # the workspace that points at it.
    outside_target = tmp_path.parent / "outside-target"
    outside_target.mkdir(parents=True, exist_ok=True)
    try:
        symlink_inside = tmp_path / "looks-inside"
        symlink_inside.symlink_to(outside_target, target_is_directory=True)

        sbom_path = tmp_path / "sbom.cdx.json"
        sbom_path.write_text("{}", encoding="utf-8")

        with pytest.raises(ValueError) as excinfo:
            trivy_adapter.run_trivy_sbom(
                sbom_path=sbom_path,
                output_dir=symlink_inside / "trivy",
            )
        assert "escapes workspace root" in str(excinfo.value)
    finally:
        # Best-effort cleanup; pytest's tmp_path teardown handles the rest.
        outside_target_marker = outside_target / "trivy"
        if outside_target_marker.exists():
            outside_target_marker.rmdir()
        if outside_target.exists():
            outside_target.rmdir()


# ---------------------------------------------------------------------------
# L2 — Output JSON size cap (256 MiB)
# ---------------------------------------------------------------------------


def test_load_json_within_size_cap_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A normal-sized report loads without complaint.

    Drives the full real-mode path so the size check happens inside
    ``run_trivy_sbom`` (where the cap actually fires), not in isolation.
    """
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps({"SchemaVersion": 2, "Results": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )
    assert result.report["Results"] == []


def test_load_json_exceeding_cap_raises_trivy_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file past the 256 MiB cap is refused before ``json.load`` runs.

    We monkeypatch ``Path.stat`` to lie about the file size so we don't have
    to actually write 256 MiB of bytes (which would be slow + flake-prone in
    constrained CI).
    """
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    # Sentinel ``st_size`` past the cap. We use the os.stat_result tuple
    # constructor to keep the rest of the stat fields realistic.
    real_stat = Path.stat
    over_cap = 256 * 1024 * 1024 + 1

    class FakeStat:
        st_size = over_cap

    def fake_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        # Only the trivy-output file lies; everything else (sbom_path,
        # tmp_path) keeps the real size so the path-guard / mkdir paths work.
        if self.name == "trivy-sbom.json":
            return FakeStat()
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps({"SchemaVersion": 2, "Results": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(trivy_adapter.TrivyFailed) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path, output_dir=tmp_path / "trivy"
        )
    msg = str(excinfo.value)
    assert "too large" in msg
    assert str(over_cap) in msg
    # safe_detail surfaces the size-cap reason without exposing the path.
    assert excinfo.value.safe_detail is not None
    assert "oversized" in excinfo.value.safe_detail.lower()
    # tmp_path absolute path MUST NOT appear in safe_detail (L4 contract).
    assert str(tmp_path) not in excinfo.value.safe_detail


def test_load_json_empty_file_raises_json_decode_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A 0-byte report passes the size cap but fails on JSON parsing.

    Pins that the size cap is an *upper* bound only; the existing
    ``json.JSONDecodeError`` behaviour for malformed / empty files is
    preserved so callers can still distinguish "trivy crashed mid-write"
    (decode error) from "trivy went haywire and wrote a 1 GiB blob"
    (TrivyFailed size cap).
    """
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        # 0-byte file.
        Path(cmd[out_idx]).write_bytes(b"")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(json.JSONDecodeError):
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path, output_dir=tmp_path / "trivy"
        )


# ---------------------------------------------------------------------------
# L4 — Error message path redaction (TrivyFailed / TrivyTimeout safe_detail)
# ---------------------------------------------------------------------------


def test_trivy_failed_default_safe_detail() -> None:
    """Without an explicit ``safe_detail`` kwarg, a sensible default applies.

    This is the contract callers rely on so they never have to defensively
    check for ``None`` when surfacing the value into an RFC 7807 response.
    """
    from integrations import trivy as trivy_adapter

    exc = trivy_adapter.TrivyFailed("trivy exited 2: /opt/trustedoss/secret-path")
    assert exc.safe_detail == "Vulnerability scan failed"
    # The full diagnostic message is preserved on str(exc) for ops logs.
    assert "/opt/trustedoss/secret-path" in str(exc)
    assert exc.safe_detail is not None
    assert "/opt/trustedoss/secret-path" not in exc.safe_detail


def test_trivy_timeout_default_safe_detail() -> None:
    """``TrivyTimeout`` exposes the same ``safe_detail`` contract."""
    from integrations import trivy as trivy_adapter

    exc = trivy_adapter.TrivyTimeout(
        "trivy sbom exceeded 1800s scanning /opt/trustedoss/workspace/uuid/sbom.json"
    )
    assert exc.safe_detail == "Vulnerability scan timed out"
    assert "/opt/trustedoss/workspace" in str(exc)
    assert exc.safe_detail is not None
    assert "/opt/trustedoss/workspace" not in exc.safe_detail


def test_run_trivy_sbom_failure_safe_detail_uses_basename_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real-mode SBOM scan failure exposes ``safe_detail`` with basename only.

    The absolute path of the SBOM (which encodes scan UUIDs and the
    workspace layout) must never appear in ``safe_detail`` because callers
    surface it to API clients. Only the basename — which the client already
    chose / can derive — is safe to echo back.
    """
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "scan-uuid-1234" / "deeply-nested" / "sbom.cdx.json"
    sbom_path.parent.mkdir(parents=True)
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=2,
            stdout=b"",
            stderr=b"trivy: db error",
        )

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(trivy_adapter.TrivyFailed) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path, output_dir=tmp_path / "trivy"
        )

    safe = excinfo.value.safe_detail
    assert safe is not None
    # Basename is OK to expose — the client gave us the file.
    assert sbom_path.name in safe
    # Absolute path / parent dirs (which encode workspace layout + scan UUID)
    # must NOT be in safe_detail.
    assert str(sbom_path) not in safe
    assert "scan-uuid-1234" not in safe
    assert "deeply-nested" not in safe
    # But the main exception message still has the full diagnostic for ops.
    assert "db error" in str(excinfo.value)


def test_run_trivy_sbom_timeout_safe_detail_uses_basename_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``TrivyTimeout`` raised by ``run_trivy_sbom`` also redacts paths."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "scan-uuid-9999" / "sbom.cdx.json"
    sbom_path.parent.mkdir(parents=True)
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(trivy_adapter.TrivyTimeout) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path,
            output_dir=tmp_path / "trivy",
            timeout_seconds=11,
        )

    safe = excinfo.value.safe_detail
    assert safe is not None
    assert sbom_path.name in safe
    assert "scan-uuid-9999" not in safe
    assert str(sbom_path.parent) not in safe


def test_run_trivy_image_failure_safe_detail_includes_image_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``run_trivy_image`` ``safe_detail`` includes ``image_ref``.

    Unlike absolute SBOM paths, the image ref is caller-supplied and visible
    in the API response that spawned the scan — echoing it back is safe and
    informative ("which image failed?").
    """
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd, returncode=3, stdout=b"", stderr=b"trivy: registry auth failed"
        )

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(trivy_adapter.TrivyFailed) as excinfo:
        trivy_adapter.run_trivy_image(
            image_ref="ghcr.io/private/img:1.2.3",
            output_dir=tmp_path / "trivy",
        )

    safe = excinfo.value.safe_detail
    assert safe is not None
    assert "ghcr.io/private/img:1.2.3" in safe
    # And the workspace path the worker wrote to is NOT in safe_detail.
    assert str(tmp_path) not in safe
