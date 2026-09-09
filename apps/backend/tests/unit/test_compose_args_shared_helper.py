# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
scripts/lib/compose_args.sh's compose_args_from_env() (#441).

Neither this helper nor the inline logic it replaced in scripts/upgrade.sh
had ever been executed by anything CI runs - both were exercised only by an
operator actually deploying. deploy/hetzner/remote-deploy.sh's own
crash-recovery restart hard-coded `-f docker-compose.yml` with no COMPOSE_FILE
handling at all before this change (#441), which this repo's shellcheck gate
cannot catch (it checks syntax, not which env var wins). This drives the real
function through subprocess with a real temp directory and .env file rather
than re-describing the bash in Python, which would test the description
instead of the shell.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSE_ARGS_SH = REPO_ROOT / "scripts" / "lib" / "compose_args.sh"


def _compose_args(tmp_path: Path, *, env: dict[str, str] | None = None) -> list[str]:
    """Source the helper and call compose_args_from_env() in tmp_path, cwd'd
    there the way every real caller (upgrade.sh, remote-deploy.sh) already
    does, and return the resulting COMPOSE_ARGS elements."""
    script = (
        f'cd "{tmp_path}" && source "{COMPOSE_ARGS_SH}" && compose_args_from_env '
        '&& printf "%s\\n" "${COMPOSE_ARGS[@]}"'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=True,
    )
    return result.stdout.splitlines()


def test_no_env_file_and_no_compose_file_var_defaults_to_the_base_file(
    tmp_path: Path,
) -> None:
    assert _compose_args(tmp_path, env={"PATH": "/usr/bin:/bin"}) == ["-f", "docker-compose.yml"]


def test_compose_file_already_in_the_environment_wins_over_env_file(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("COMPOSE_FILE=ignored.yml\n")
    args = _compose_args(
        tmp_path,
        env={"PATH": "/usr/bin:/bin", "COMPOSE_FILE": "docker-compose.yml:docker-compose.demo.yml"},
    )
    assert args == ["-f", "docker-compose.yml", "-f", "docker-compose.demo.yml"]


def test_compose_file_declared_only_in_dot_env_is_picked_up(tmp_path: Path) -> None:
    """The actual bug this PR fixes: a deployment declares its overlay in
    .env (the documented way), not as a shell-exported variable."""
    (tmp_path / ".env").write_text("COMPOSE_FILE=docker-compose.yml:docker-compose.demo.yml\n")
    args = _compose_args(tmp_path, env={"PATH": "/usr/bin:/bin"})
    assert args == ["-f", "docker-compose.yml", "-f", "docker-compose.demo.yml"]


def test_dot_env_with_no_compose_file_line_defaults_to_the_base_file(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("SOME_OTHER_KEY=value\n")
    assert _compose_args(tmp_path, env={"PATH": "/usr/bin:/bin"}) == ["-f", "docker-compose.yml"]


def test_a_trailing_colon_does_not_produce_an_empty_dash_f(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("COMPOSE_FILE=docker-compose.yml:\n")
    args = _compose_args(tmp_path, env={"PATH": "/usr/bin:/bin"})
    assert args == ["-f", "docker-compose.yml"]


def test_a_compose_file_that_resolves_to_nothing_falls_back_to_the_base_file(
    tmp_path: Path,
) -> None:
    """COMPOSE_FILE set but entirely colons (":::") is the pathological
    input that must not leave COMPOSE_ARGS empty - an empty array means every
    caller's `docker-compose "${COMPOSE_ARGS[@]}" up -d` runs with no -f at
    all, silently falling through to compose's own default file discovery."""
    args = _compose_args(tmp_path, env={"PATH": "/usr/bin:/bin", "COMPOSE_FILE": ":::"})
    assert args == ["-f", "docker-compose.yml"]
