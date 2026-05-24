"""
ProjectCreate / ProjectUpdate.default_branch — git-ref-safe validator.

``default_branch`` is forwarded to the b3 remediation-PR service, where it is
interpolated into GitHub API URL paths/queries and the PR ``base`` field. This
file pins the API-boundary (defence-in-depth) guard: a value that is not a
git-ref-safe name is rejected as a 422 ValidationError before it can ever be
stored — so the b3 path never sees an injectable branch name from a fresh write.

The b3 service additionally re-validates at its own trust boundary
(``_validate_base_branch``) for the corrupted/legacy-row case; that is covered in
``tests/integration/test_remediation_pr_service.py``.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from schemas.scan import ProjectCreate, ProjectUpdate

# ---------------------------------------------------------------------------
# Accepted (git-ref-safe)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "master",
        "release/1.x",
        "feature/foo-bar_baz.1",
        "develop",
    ],
)
def test_valid_default_branch_is_accepted(branch: str) -> None:
    project = ProjectCreate(team_id=uuid.uuid4(), name="ok", slug="ok", default_branch=branch)
    assert project.default_branch == branch
    upd = ProjectUpdate(default_branch=branch)
    assert upd.default_branch == branch


def test_blank_default_branch_normalises_to_none() -> None:
    """An empty/whitespace value falls back to the server default (None here)."""
    blank = ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s", default_branch="   ")
    assert blank.default_branch is None
    assert ProjectUpdate(default_branch="").default_branch is None
    assert ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s").default_branch is None


def test_explicit_none_default_branch_is_accepted() -> None:
    """An explicit ``None`` passes the validator unchanged (no branch set)."""
    assert ProjectCreate(
        team_id=uuid.uuid4(), name="n", slug="s", default_branch=None
    ).default_branch is None
    assert ProjectUpdate(default_branch=None).default_branch is None


# ---------------------------------------------------------------------------
# Rejected (injection / traversal / control chars)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "branch",
    [
        "main&injected=1",   # query-param smuggle into the GitHub URL
        "main?ref=evil",     # query smuggle
        "main#frag",         # fragment smuggle
        "main /../admin",    # space + traversal
        "main/../admin",     # path traversal (no space)
        "/leading",          # leading slash → empty path segment
        "main\r\nHost: evil",  # CRLF smuggle
        "main\tx",           # control char (tab)
        "main x",            # space
        "..",                # bare traversal
    ],
)
def test_malicious_default_branch_is_rejected(branch: str) -> None:
    with pytest.raises(ValidationError):
        ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s", default_branch=branch)
    with pytest.raises(ValidationError):
        ProjectUpdate(default_branch=branch)


def test_overlong_default_branch_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProjectCreate(
            team_id=uuid.uuid4(), name="n", slug="s", default_branch="a" * 300
        )
