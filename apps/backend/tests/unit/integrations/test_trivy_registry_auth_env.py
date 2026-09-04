# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The credential reaches Trivy as a path, never as a value (ER3).

``scrubbed_env_for_trivy`` strips ``TRIVY_USERNAME`` / ``TRIVY_PASSWORD`` on
purpose: Trivy parses attacker-influenced images, so a parser bug or an
error-path lookup must have no credential in the process environment to carry
out. Adding registry auth must not reopen that, which is why the environment
gains only ``DOCKER_CONFIG`` and the secret lives in a 0600 file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.trivy import _image_env


def test_no_docker_config_when_there_is_no_credential() -> None:
    env = _image_env(None)
    assert "DOCKER_CONFIG" not in env


def test_docker_config_is_a_path(tmp_path: Path) -> None:
    env = _image_env(tmp_path)
    assert env["DOCKER_CONFIG"] == str(tmp_path)


def test_the_credential_band_stays_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guarantee this feature must not weaken.

    Even with registry auth in play, a username or password set in the worker's
    own environment must not be forwarded to Trivy.
    """
    monkeypatch.setenv("TRIVY_USERNAME", "registry-user")
    monkeypatch.setenv("TRIVY_PASSWORD", "registry-pass")

    env = _image_env(tmp_path)

    assert "TRIVY_USERNAME" not in env
    assert "TRIVY_PASSWORD" not in env
    # And nothing else smuggled the values in under another name.
    assert "registry-pass" not in "".join(env.values())
