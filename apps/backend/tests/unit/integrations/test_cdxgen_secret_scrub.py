"""
cdxgen adapter -- credential scrubbing on a failed run (security review HIGH,
private-registry-auth-mount PR).

cdxgen shells out to pip / npm / Maven, which can be pointed at a private
registry via the mounted ``PIP_CONFIG_FILE`` / ``MVN_ARGS`` / npmrc
conventions this PR adds. When that registry rejects the request (typo,
expired token, transient 401/403), the resolver's stderr can echo the full
request URL -- including a ``scheme://user:pass@host`` userinfo credential --
verbatim. Before this fix, ``run_cdxgen`` copied ``completed.stderr`` straight
into ``CdxgenFailed``'s message; the generic exception handler in
``tasks/scan_source.py`` then stores that message, unmodified, in
``scan.error_message`` -- a field any team member can read via
``GET /api/v1/scans/{id}``.

These tests pin two things:

  1. The exact reproduction scenario from the security review (a pip
     index-url with embedded credentials, echoed on a 401) never survives
     into the ``CdxgenFailed`` message.
  2. The structured ``cdxgen_failed`` log line is scrubbed the same way, so
     the credential does not leak through structlog either.

We monkeypatch ``run_with_line_streaming`` (same pattern as
``test_cdxgen_cocoapods_exclude.py``) rather than spawning a real
subprocess -- cdxgen is not guaranteed to be on the host running unit tests.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from integrations import cdxgen


def _fail_with_stderr(stderr: bytes) -> Any:
    def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"", stderr=stderr)

    return _fake_run


@pytest.fixture
def cdxgen_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.cdxgen.shutil.which", lambda name: f"/usr/bin/{name}"
    )


def test_pip_private_registry_credential_scrubbed_from_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cdxgen_present: None
) -> None:
    """Reproduces the security review scenario verbatim.

    A ``PIP_CONFIG_FILE``-pointed ``index-url`` with embedded credentials,
    failing with a 401, echoes the whole URL (userinfo included) into pip's
    stderr, which cdxgen's own subprocess call surfaces unmodified.
    """
    stderr = (
        b"ERROR: Could not find a version that satisfies the requirement foo\n"
        b"ERROR: 401 Client Error: Unauthorized for url: "
        b"https://svc-user:S3cr3tToken@pypi.internal/simple/foo/\n"
    )
    monkeypatch.setattr(cdxgen, "run_with_line_streaming", _fail_with_stderr(stderr))

    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(cdxgen.CdxgenFailed) as exc_info:
        cdxgen.run_cdxgen(source_dir=source, output_dir=tmp_path / "out", backend="real")

    message = str(exc_info.value)
    # The raw credential (and its username) must never survive.
    assert "S3cr3tToken" not in message
    assert "svc-user" not in message
    # Diagnostic value is preserved: an operator can still see which host
    # and what kind of failure occurred.
    assert "pypi.internal" in message
    assert "401" in message
    assert "Unauthorized" in message


def test_pip_private_registry_credential_scrubbed_from_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cdxgen_present: None
) -> None:
    """The structured ``cdxgen_failed`` log line must be scrubbed too --
    not just the exception message. cdxgen's own ``log.error`` call used to
    embed the same raw stderr independently of the exception.
    """
    stderr = b"https://svc-user:S3cr3tToken@pypi.internal/simple/foo/ 401\n"
    monkeypatch.setattr(cdxgen, "run_with_line_streaming", _fail_with_stderr(stderr))

    source = tmp_path / "source"
    source.mkdir()

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(cdxgen.CdxgenFailed):
            cdxgen.run_cdxgen(source_dir=source, output_dir=tmp_path / "out", backend="real")

    failed_events = [evt for evt in captured if evt.get("event") == "cdxgen_failed"]
    assert failed_events, "expected a cdxgen_failed log event"
    logged_stderr = failed_events[0].get("stderr", "")
    assert "S3cr3tToken" not in logged_stderr
    assert "svc-user" not in logged_stderr
    assert "pypi.internal" in logged_stderr


def test_maven_server_credential_scrubbed_from_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cdxgen_present: None
) -> None:
    """Same defence, Maven shape: a resolved ``settings.xml`` server
    password echoed by a failed ``mvn`` transfer.
    """
    stderr = (
        b"[ERROR] Failed to execute goal on project foo: Could not resolve "
        b"dependencies: Could not transfer artifact from/to internal "
        b"(https://mvn-user:hunter2@maven.internal/repo): "
        b"authentication failed for https://mvn-user:hunter2@maven.internal/repo, "
        b"status: 401 Unauthorized\n"
    )
    monkeypatch.setattr(cdxgen, "run_with_line_streaming", _fail_with_stderr(stderr))

    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(cdxgen.CdxgenFailed) as exc_info:
        cdxgen.run_cdxgen(source_dir=source, output_dir=tmp_path / "out", backend="real")

    message = str(exc_info.value)
    assert "hunter2" not in message
    assert "mvn-user" not in message
    assert "maven.internal" in message
    assert "401" in message


def test_clean_stderr_is_not_mangled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cdxgen_present: None
) -> None:
    """A failure with no credential-shaped content must pass through
    unscathed -- the scrubber must not eat ordinary diagnostic text.
    """
    stderr = b"Error: ENOENT: no such file or directory, open 'package.json'\n"
    monkeypatch.setattr(cdxgen, "run_with_line_streaming", _fail_with_stderr(stderr))

    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(cdxgen.CdxgenFailed) as exc_info:
        cdxgen.run_cdxgen(source_dir=source, output_dir=tmp_path / "out", backend="real")

    assert "ENOENT" in str(exc_info.value)
    assert "package.json" in str(exc_info.value)
