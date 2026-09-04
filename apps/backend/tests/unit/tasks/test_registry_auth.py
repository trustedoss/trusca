# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Registry credential materialisation (ER3).

Three properties matter more than the happy path, and each has a specific way
of going wrong:

* the file must never land inside the scan workspace, because backup.sh tars
  that whole tree and the credential would be in every archive;
* it must be gone after a FAILED scan, not just a successful one;
* two scans running at once must not see each other's credentials.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from base64 import b64decode
from pathlib import Path
from typing import cast

import pytest

from services.registry_allowlist import DOCKER_HUB_AUTH_KEY
from tasks._registry_auth import (
    RegistryAuthLocationError,
    build_docker_config,
    registry_auth_dir,
    sweep_stale_auth_dirs,
)

CREDS = {"ghcr.io": ("bot", "s3cr3t-token")}


def test_config_is_keyed_by_registry_host() -> None:
    """What binds a credential to one registry.

    Trivy sends an entry only when pulling from its host, so a credential for
    one registry is never offered to another.
    """
    config = build_docker_config(
        {"ghcr.io": ("u1", "p1"), "registry.example.com": ("u2", "p2")}
    )
    auths = cast("dict[str, dict[str, str]]", config["auths"])
    assert set(auths) == {"ghcr.io", "registry.example.com"}
    assert b64decode(auths["ghcr.io"]["auth"]).decode() == "u1:p1"
    assert b64decode(auths["registry.example.com"]["auth"]).decode() == "u2:p2"


def test_no_credentials_yields_no_directory() -> None:
    """The caller then passes no DOCKER_CONFIG at all, rather than an empty one."""
    with registry_auth_dir({}, scan_id=uuid.uuid4()) as path:
        assert path is None


def test_the_file_is_written_private() -> None:
    with registry_auth_dir(CREDS, scan_id=uuid.uuid4()) as path:
        assert path is not None
        config_path = path / "config.json"
        assert json.loads(config_path.read_text())["auths"]["ghcr.io"]["auth"]
        # 0600 on the file and 0700 on the directory: another user on the same
        # host must not be able to read the credential.
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_the_directory_is_removed_after_a_successful_scan() -> None:
    with registry_auth_dir(CREDS, scan_id=uuid.uuid4()) as path:
        assert path is not None
        seen = path
    assert not seen.exists()


def test_the_directory_is_removed_after_a_FAILED_scan() -> None:
    """The path that gets forgotten. Cleanup on success is easy; this is not."""
    seen: Path | None = None
    with pytest.raises(RuntimeError, match="trivy exploded"):
        with registry_auth_dir(CREDS, scan_id=uuid.uuid4()) as path:
            seen = path
            raise RuntimeError("trivy exploded")
    assert seen is not None
    assert not seen.exists(), "a failed scan left the credential on disk"


def test_two_scans_do_not_share_a_directory() -> None:
    """Scans run concurrently; one scan's credential must not be in another's."""
    with registry_auth_dir(CREDS, scan_id=uuid.uuid4()) as first:
        with registry_auth_dir(
            {"other.example.com": ("u", "p")}, scan_id=uuid.uuid4()
        ) as second:
            assert first is not None and second is not None
            assert first != second
            first_auths = json.loads((first / "config.json").read_text())["auths"]
            second_auths = json.loads((second / "config.json").read_text())["auths"]
            assert set(first_auths) == {"ghcr.io"}
            assert set(second_auths) == {"other.example.com"}
        # The inner scan finishing must not take the outer scan's file with it.
        assert (first / "config.json").exists()


def test_a_workspace_temp_root_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The condition that cannot be settled by choosing a path once.

    WORKSPACE_HOST_PATH is operator-configurable, so a deployment can put the
    workspace around whatever temp root we picked. backup.sh tars the whole
    workspace, so writing here would put the credential in every archive.
    Failing the scan is the correct outcome.
    """
    workspace = tmp_path / "workspace"
    inside = workspace / "tmp"
    inside.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(workspace))
    monkeypatch.setattr("tasks._registry_auth._temp_root", lambda: inside)

    with pytest.raises(RegistryAuthLocationError) as excinfo:
        with registry_auth_dir(CREDS, scan_id=uuid.uuid4()):
            pass
    # The message has to say what to change, or an operator cannot act on it.
    assert "WORKSPACE_HOST_PATH" in str(excinfo.value) or "workspace" in str(excinfo.value)


def test_a_temp_root_outside_the_workspace_is_fine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard must not reject a legitimate sibling directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "scratch"
    outside.mkdir()
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(workspace))
    monkeypatch.setattr("tasks._registry_auth._temp_root", lambda: outside)

    with registry_auth_dir(CREDS, scan_id=uuid.uuid4()) as path:
        assert path is not None


def test_the_sweep_removes_what_a_sigkill_left_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SIGKILL skips the finally, so this is the admitted gap being closed."""
    monkeypatch.setattr("tasks._registry_auth._temp_root", lambda: tmp_path)
    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "3900")

    stale = tmp_path / "trusca-registry-auth-old"
    stale.mkdir()
    (stale / "config.json").write_text("{}")
    old = 1_000_000.0
    os.utime(stale, (old, old))

    fresh = tmp_path / "trusca-registry-auth-live"
    fresh.mkdir()

    unrelated = tmp_path / "something-else"
    unrelated.mkdir()
    os.utime(unrelated, (old, old))

    removed = sweep_stale_auth_dirs()

    assert removed == 1
    assert not stale.exists()
    # A directory younger than the hard time limit may belong to a scan that is
    # still running; removing it would break a live pull.
    assert fresh.exists()
    # Only directories this module owns, matched by prefix.
    assert unrelated.exists()


def test_the_sweep_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sweep failure must not fail the scan that called it."""

    def _boom() -> Path:
        raise OSError("temp root unavailable")

    monkeypatch.setattr("tasks._registry_auth._temp_root", _boom)
    assert sweep_stale_auth_dirs() == 0


@pytest.mark.parametrize("host", ["docker.io", "index.docker.io"])
def test_docker_hub_is_written_under_the_key_trivy_actually_reads(host: str) -> None:
    """Docker Hub is the one registry whose auth key is not its hostname.

    Keyed by the host, the credential is never consulted: the pull gets an
    anonymous token and fails with a permission error that reads exactly like a
    wrong password, so nothing about the symptom points at the key. Measured
    against Trivy 0.71.2.
    """
    config = build_docker_config({host: ("bot", "hub-token")})
    auths = cast("dict[str, dict[str, str]]", config["auths"])

    assert set(auths) == {DOCKER_HUB_AUTH_KEY}
    # The host spelling must NOT be what lands in the file. Asserting only that
    # the v1 key is present would pass while the dead entry sat beside it.
    assert host not in auths
    assert b64decode(auths[DOCKER_HUB_AUTH_KEY]["auth"]).decode() == "bot:hub-token"


def test_a_hub_credential_and_another_registry_stay_separate() -> None:
    """The rewrite must not collapse two registries into one entry."""
    config = build_docker_config(
        {"docker.io": ("hubuser", "hubpass"), "ghcr.io": ("ghuser", "ghpass")}
    )
    auths = cast("dict[str, dict[str, str]]", config["auths"])
    assert set(auths) == {DOCKER_HUB_AUTH_KEY, "ghcr.io"}
    assert b64decode(auths["ghcr.io"]["auth"]).decode() == "ghuser:ghpass"


def test_the_sweep_leaves_a_live_scan_alone_when_the_limit_is_lowered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The hard limit is read at call time, so lowering it must not make the
    next scan's sweep delete a running scan's credentials out from under it."""
    monkeypatch.setattr("tasks._registry_auth._temp_root", lambda: tmp_path)
    # The effective hard limit is clamped to soft + grace, so the soft limit
    # has to come down too for the lowered value to be the one in force.
    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "600")

    live = tmp_path / "trusca-registry-auth-live"
    live.mkdir()
    started = 10_000_000.0
    os.utime(live, (started, started))

    # A scan that started 5000s ago under a two-hour limit is plainly live.
    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "7200")
    now = started + 5000
    assert sweep_stale_auth_dirs(now=now) == 0
    assert live.exists()

    # The operator now lowers the limit to 30 minutes. The running scan does
    # not stop, and without the margin this sweep would delete its credentials
    # (cutoff would be started + 3200, past the directory's mtime).
    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "1800")
    assert sweep_stale_auth_dirs(now=now) == 0
    assert live.exists()

    # Far enough past the limit that no task can still hold it.
    assert sweep_stale_auth_dirs(now=started + 1800 + 3600 + 100) == 1
    assert not live.exists()
