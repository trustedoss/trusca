"""
ProjectCreate.git_url — SSRF guard integration through the schema.

The Pydantic field_validator delegates to `core.url_guard.validate_git_url`.
This module pins the wiring:

  - A well-formed public URL constructs the model successfully.
  - SSRF candidates raise ValidationError (the GitUrlValidationError is a
    ValueError subclass so Pydantic surfaces it as a regular validation
    failure that the FastAPI handler maps to 422 problem+json).

Lower-level URL semantics are covered by `test_url_guard.py`; this file is
the contract between the schema and the guard.
"""

from __future__ import annotations

import socket
import uuid

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Public URL — accepted
# ---------------------------------------------------------------------------


def test_public_https_git_url_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    from schemas.scan import ProjectCreate

    monkeypatch.setattr(
        "core.url_guard.socket.getaddrinfo",
        lambda host, port: [(socket.AF_INET, 0, 0, "", ("140.82.121.4", 0))],
    )
    project = ProjectCreate(
        team_id=uuid.uuid4(),
        name="ok",
        slug="ok",
        git_url="https://github.com/foo/bar.git",
    )
    assert project.git_url == "https://github.com/foo/bar.git"


# ---------------------------------------------------------------------------
# SSRF candidates — rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.0.1/repo",
        "http://127.0.0.1/repo",
        "http://169.254.169.254/latest/meta-data/",
        "https://metadata.google.internal/",
        "file:///etc/passwd",
    ],
)
def test_ssrf_candidates_are_rejected_at_schema(url: str) -> None:
    from schemas.scan import ProjectCreate

    with pytest.raises(ValidationError):
        ProjectCreate(
            team_id=uuid.uuid4(),
            name="n",
            slug="s",
            git_url=url,
        )


def test_overlong_git_url_is_rejected_at_schema() -> None:
    """The 2048 cap is enforced at both the field level and inside the guard."""
    from schemas.scan import ProjectCreate

    with pytest.raises(ValidationError):
        ProjectCreate(
            team_id=uuid.uuid4(),
            name="n",
            slug="s",
            git_url="https://example.com/" + "x" * 3000,
        )


def test_ssh_form_passes_through_when_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from schemas.scan import ProjectCreate

    monkeypatch.setattr(
        "core.url_guard.socket.getaddrinfo",
        lambda host, port: [(socket.AF_INET, 0, 0, "", ("140.82.121.4", 0))],
    )
    project = ProjectCreate(
        team_id=uuid.uuid4(),
        name="n",
        slug="s",
        git_url="git@github.com:foo/bar.git",
    )
    assert project.git_url == "git@github.com:foo/bar.git"
