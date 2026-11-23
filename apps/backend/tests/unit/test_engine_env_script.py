# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""scripts/lib/engine_env.sh: what install.sh and upgrade.sh do for the BomLens engine.

The engine is the default scan engine under docker-compose.yml, so an install or
an upgrade has to leave .env with the volume names the engine mounts and the host
with the network its containers join, without touching a value an operator set,
and an air-gapped install has to keep the in-process engine. The script runs here
against a throwaway directory and a fake ``docker`` on PATH.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "lib" / "engine_env.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not installed")


def _fake_docker(tmp_path: Path, *, answers: bool = True, network_exists: bool = False) -> Path:
    """A docker that logs every call and answers ``info`` and ``network`` as told."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    script = bindir / "docker"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        f'if [ "$1" = info ]; then exit {0 if answers else 1}; fi\n'
        f'if [ "$1 $2" = "network inspect" ]; then exit {0 if network_exists else 1}; fi\n'
        "exit 0\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bindir


def _run(
    tmp_path: Path, env_text: str | None, *args: str, docker: Path | None = None
) -> tuple[str, str]:
    project = tmp_path / "My Portal"
    project.mkdir()
    if env_text is not None:
        (project / ".env").write_text(env_text)
    bindir = docker or _fake_docker(tmp_path)
    environ = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROJECT_NAME"}
    environ["PATH"] = f"{bindir}:{environ['PATH']}"
    assert BASH is not None
    subprocess.run(  # noqa: S603 - fixed argv, test only
        [BASH, "-c", f'source "{SCRIPT}"; engine_env_prepare {" ".join(args)}'],
        cwd=project,
        env=environ,
        capture_output=True,
        text=True,
        check=True,
    )
    log = tmp_path / "docker.log"
    return (project / ".env").read_text(), log.read_text() if log.exists() else ""


def test_the_volume_names_follow_the_compose_project_name(tmp_path: Path) -> None:
    """docker-compose lower-cases the directory name and drops what a project name
    cannot hold; the volumes are ``<project>_workspace`` and ``<project>_trivy-cache``."""
    env, _ = _run(tmp_path, "FOO=1\n")
    assert "SCAN_WORKSPACE_VOLUME=myportal_workspace\n" in env
    assert "SCAN_TRIVY_CACHE_VOLUME=myportal_trivy-cache\n" in env
    assert env.startswith("FOO=1\n")


def test_a_project_name_in_the_env_file_wins_over_the_directory(tmp_path: Path) -> None:
    env, _ = _run(tmp_path, "COMPOSE_PROJECT_NAME=Trusca\n")
    assert "SCAN_WORKSPACE_VOLUME=trusca_workspace\n" in env


def test_a_value_the_operator_set_is_never_overwritten(tmp_path: Path) -> None:
    env, _ = _run(
        tmp_path,
        "SCAN_WORKSPACE_VOLUME=mine_ws\nSCAN_TRIVY_CACHE_VOLUME=mine_trivy\nSCAN_ENGINE=bomlens\n",
    )
    assert env.count("SCAN_WORKSPACE_VOLUME=") == 1 and "mine_ws" in env
    assert env.count("SCAN_TRIVY_CACHE_VOLUME=") == 1 and "mine_trivy" in env
    assert "SCAN_ENGINE=bomlens" in env


def test_an_empty_key_from_the_example_file_is_filled_not_duplicated(tmp_path: Path) -> None:
    env, _ = _run(tmp_path, "SCAN_WORKSPACE_VOLUME=\nOTHER=x\nSCAN_TRIVY_CACHE_VOLUME=\n")
    assert env.count("SCAN_WORKSPACE_VOLUME=") == 1
    assert "SCAN_WORKSPACE_VOLUME=myportal_workspace\n" in env
    assert env.count("SCAN_TRIVY_CACHE_VOLUME=") == 1
    assert "OTHER=x\n" in env


def test_the_egress_network_is_created_once_without_traffic_between_its_containers(
    tmp_path: Path,
) -> None:
    _, log = _run(tmp_path, "")
    assert (
        "network create --opt com.docker.network.bridge.enable_icc=false trustedoss-scan-egress"
        in log
    )


def test_an_existing_network_is_left_alone(tmp_path: Path) -> None:
    _, log = _run(tmp_path, "", docker=_fake_docker(tmp_path, network_exists=True))
    assert "network create" not in log


def test_the_network_named_in_the_env_file_is_the_one_created(tmp_path: Path) -> None:
    _, log = _run(tmp_path, "SCAN_BOMLENS_NETWORK=my-egress\n")
    assert "my-egress" in log and "trustedoss-scan-egress" not in log


def test_without_a_docker_daemon_the_script_still_finishes_and_says_what_to_do(
    tmp_path: Path,
) -> None:
    env, log = _run(tmp_path, "", docker=_fake_docker(tmp_path, answers=False))
    assert "network create" not in log
    assert "SCAN_WORKSPACE_VOLUME=" in env


def test_an_offline_install_keeps_the_in_process_engine(tmp_path: Path) -> None:
    env, _ = _run(tmp_path, "", "offline")
    assert "SCAN_ENGINE=inprocess\n" in env
    assert "SCAN_CONTAINER_ENGINE=inprocess\n" in env


def test_an_offline_install_does_not_override_an_engine_the_operator_chose(
    tmp_path: Path,
) -> None:
    env, _ = _run(tmp_path, "SCAN_ENGINE=bomlens\n", "offline")
    assert "SCAN_ENGINE=bomlens\n" in env and "SCAN_ENGINE=inprocess" not in env


def test_an_older_air_gapped_install_keeps_the_in_process_engine_on_upgrade(
    tmp_path: Path,
) -> None:
    """It has no SCAN_ENGINE of its own, and following the new default would send
    its scans to an engine that cannot pull its images."""
    env, _ = _run(tmp_path, "TRIVY_DB_BOOTSTRAP_ON_START=false\n")
    assert "SCAN_ENGINE=inprocess\n" in env
    assert "SCAN_CONTAINER_ENGINE=inprocess\n" in env


def test_a_connected_install_is_left_on_the_default_engine(tmp_path: Path) -> None:
    env, _ = _run(tmp_path, "FOO=1\n")
    assert "SCAN_ENGINE" not in env and "SCAN_CONTAINER_ENGINE" not in env


def test_install_and_upgrade_both_call_it_before_the_stack_starts() -> None:
    install = (REPO_ROOT / "scripts" / "install.sh").read_text()
    upgrade = (REPO_ROOT / "scripts" / "upgrade.sh").read_text()
    assert install.index("engine_env_prepare") < install.index("up -d postgres redis backend")
    assert 'engine_env_prepare offline' in install
    assert upgrade.index("engine_env_prepare") < upgrade.index('"${COMPOSE_ARGS[@]}" up -d')
