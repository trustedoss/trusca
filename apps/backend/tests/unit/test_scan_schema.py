"""
Unit tests for `schemas/scan.py` — Phase 2 PR #7.

These run with no DB. They pin the public contract of the inbound and outbound
shapes:

  - ProjectCreate slug regex (lowercase + dashes only)
  - ProjectCreate git_url accepts https/ssh/scp-like, rejects junk
  - ProjectCreate visibility: only 'team' is currently writable; 'organization'
    and any other value must 422 at the schema layer
  - ProjectCreate `extra='forbid'` rejects unknown fields
  - ProjectUpdate is fully optional and rejects identity fields (`team_id`,
    `slug`) at the schema layer (extra='forbid')
  - ScanCreate.kind is a closed Literal — only 'source' / 'container' pass
  - ProjectPublic.from_attributes constructs cleanly from a duck-typed ORM row
  - ScanPublic remaps the ORM attribute `scan_metadata` onto API field
    `metadata` via validation_alias
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# ProjectCreate — slug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "foo",
        "foo-bar",
        "f",
        "a1",
        "ab-cd-ef",
        "x" * 64,  # max length per StringConstraints
    ],
)
def test_project_create_accepts_valid_slug(slug: str) -> None:
    from schemas.scan import ProjectCreate

    project = ProjectCreate(team_id=uuid.uuid4(), name="ok", slug=slug)
    assert project.slug == slug


@pytest.mark.parametrize(
    "slug",
    [
        "Foo Bar",  # space + uppercase
        "foo_bar",  # underscore
        "Foo",  # uppercase
        "-foo",  # leading dash
        "foo-",  # trailing dash
        "",  # empty
        "foo!bar",  # punctuation
        "foo/bar",  # slash
        "x" * 65,  # exceeds 64-char limit
    ],
)
def test_project_create_rejects_invalid_slug(slug: str) -> None:
    from schemas.scan import ProjectCreate

    with pytest.raises(ValidationError):
        ProjectCreate(team_id=uuid.uuid4(), name="ok", slug=slug)


# ---------------------------------------------------------------------------
# ProjectCreate — git_url
# ---------------------------------------------------------------------------


def test_project_create_accepts_null_git_url() -> None:
    from schemas.scan import ProjectCreate

    project = ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s", git_url=None)
    assert project.git_url is None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/foo/bar.git",
        "http://gitlab.example.com/team/repo",
        "ssh://git@github.com/foo/bar.git",
        "git@github.com:foo/bar.git",
        "git+ssh://git@host.example.com/x/y",
        "git://example.com/repo",
    ],
)
def test_project_create_accepts_well_formed_git_url(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #8 SSRF guard does a real DNS resolution of git_url hosts, so we
    monkeypatch getaddrinfo to keep these unit cases hermetic. The IP we
    return must be a public-routable one — RFC 1918/loopback would now be
    rejected by the same guard."""
    import socket as _socket

    from schemas.scan import ProjectCreate

    monkeypatch.setattr(
        "core.url_guard.socket.getaddrinfo",
        lambda host, port: [(_socket.AF_INET, 0, 0, "", ("140.82.121.4", 0))],
    )

    project = ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s", git_url=url)
    assert project.git_url == url.strip()


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "ftp://example.com/repo",  # not a git transport we accept
        "github.com/foo/bar",  # missing scheme + no scp form
        "://broken",
        "javascript:alert(1)",
    ],
)
def test_project_create_rejects_malformed_git_url(url: str) -> None:
    from schemas.scan import ProjectCreate

    with pytest.raises(ValidationError):
        ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s", git_url=url)


def test_project_create_strips_whitespace_in_git_url() -> None:
    from schemas.scan import ProjectCreate

    project = ProjectCreate(
        team_id=uuid.uuid4(),
        name="n",
        slug="s",
        git_url="  https://example.com/foo.git  ",
    )
    assert project.git_url == "https://example.com/foo.git"


def test_project_create_treats_blank_git_url_as_none() -> None:
    from schemas.scan import ProjectCreate

    project = ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s", git_url="   ")
    assert project.git_url is None


# ---------------------------------------------------------------------------
# ProjectCreate — visibility
# ---------------------------------------------------------------------------


def test_project_create_default_visibility_is_team() -> None:
    from schemas.scan import ProjectCreate

    project = ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s")
    assert project.visibility == "team"


def test_project_create_rejects_organization_visibility_in_pr7() -> None:
    """Phase 2 only exposes 'team'; 'organization' must 422 at the schema."""
    from schemas.scan import ProjectCreate

    with pytest.raises(ValidationError):
        ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s", visibility="organization")


@pytest.mark.parametrize("bad", ["private", "public", "PUBLIC", "", "TEAM"])
def test_project_create_rejects_unknown_visibility(bad: str) -> None:
    from schemas.scan import ProjectCreate

    with pytest.raises(ValidationError):
        ProjectCreate(team_id=uuid.uuid4(), name="n", slug="s", visibility=bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ProjectCreate — extra='forbid'
# ---------------------------------------------------------------------------


def test_project_create_rejects_unknown_field() -> None:
    from schemas.scan import ProjectCreate

    with pytest.raises(ValidationError):
        ProjectCreate(  # type: ignore[call-arg]
            team_id=uuid.uuid4(),
            name="n",
            slug="s",
            is_superuser=True,  # smuggle attempt
        )


def test_project_create_strips_name_whitespace() -> None:
    from schemas.scan import ProjectCreate

    project = ProjectCreate(team_id=uuid.uuid4(), name="  hello  ", slug="hello-x")
    assert project.name == "hello"


# ---------------------------------------------------------------------------
# ProjectUpdate — fully optional + rejects identity fields
# ---------------------------------------------------------------------------


def test_project_update_all_fields_optional() -> None:
    from schemas.scan import ProjectUpdate

    update = ProjectUpdate()
    # exclude_unset is the contract used by the service
    assert update.model_dump(exclude_unset=True) == {}


def test_project_update_partial_set_only_touches_supplied_fields() -> None:
    from schemas.scan import ProjectUpdate

    update = ProjectUpdate(name="renamed")
    dumped = update.model_dump(exclude_unset=True)
    assert dumped == {"name": "renamed"}
    assert "description" not in dumped
    assert "git_url" not in dumped


def test_project_update_rejects_team_id_change() -> None:
    """Identity fields (team_id) must not be patchable — extra='forbid' guards this."""
    from schemas.scan import ProjectUpdate

    with pytest.raises(ValidationError):
        ProjectUpdate(team_id=uuid.uuid4())  # type: ignore[call-arg]


def test_project_update_rejects_slug_change() -> None:
    """Identity fields (slug) must not be patchable — extra='forbid' guards this."""
    from schemas.scan import ProjectUpdate

    with pytest.raises(ValidationError):
        ProjectUpdate(slug="new-slug")  # type: ignore[call-arg]


def test_project_update_validates_git_url_when_present() -> None:
    from schemas.scan import ProjectUpdate

    with pytest.raises(ValidationError):
        ProjectUpdate(git_url="not a url")


def test_project_update_visibility_team_ok_organization_rejected() -> None:
    from schemas.scan import ProjectUpdate

    assert ProjectUpdate(visibility="team").visibility == "team"
    with pytest.raises(ValidationError):
        ProjectUpdate(visibility="organization")


# ---------------------------------------------------------------------------
# ScanCreate — kind Literal validation + metadata default
# ---------------------------------------------------------------------------


def test_scan_create_default_kind_is_source_with_empty_metadata() -> None:
    from schemas.scan import ScanCreate

    scan = ScanCreate()
    assert scan.kind == "source"
    assert scan.metadata == {}


@pytest.mark.parametrize("kind", ["source", "container"])
def test_scan_create_accepts_known_kinds(kind: str) -> None:
    from schemas.scan import ScanCreate

    scan = ScanCreate(kind=kind)  # type: ignore[arg-type]
    assert scan.kind == kind


@pytest.mark.parametrize("kind", ["binary", "", "SOURCE", "image"])
def test_scan_create_rejects_unknown_kind(kind: str) -> None:
    from schemas.scan import ScanCreate

    with pytest.raises(ValidationError):
        ScanCreate(kind=kind)  # type: ignore[arg-type]


def test_scan_create_accepts_arbitrary_metadata_dict() -> None:
    from schemas.scan import ScanCreate

    payload: dict[str, Any] = {"git_ref": "main", "branch": "main", "depth": 1}
    scan = ScanCreate(metadata=payload)
    assert scan.metadata == payload


def test_scan_create_rejects_unknown_field() -> None:
    from schemas.scan import ScanCreate

    with pytest.raises(ValidationError):
        ScanCreate(foo="bar")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ProjectPublic — ORM round-trip
# ---------------------------------------------------------------------------


def test_project_public_from_attributes_round_trip() -> None:
    """ProjectPublic must accept duck-typed ORM rows via from_attributes=True."""
    from schemas.scan import ProjectPublic

    now = datetime.now(tz=UTC)
    pid = uuid.uuid4()
    tid = uuid.uuid4()
    fake_row = SimpleNamespace(
        id=pid,
        team_id=tid,
        name="My Project",
        slug="my-project",
        description="desc",
        git_url=None,
        default_branch="main",
        visibility="team",
        archived_at=None,
        created_by_user_id=None,
        latest_scan_id=None,
        created_at=now,
        updated_at=now,
    )

    public = ProjectPublic.model_validate(fake_row)
    assert public.id == pid
    assert public.team_id == tid
    assert public.visibility == "team"
    assert public.archived_at is None


# ---------------------------------------------------------------------------
# ScanPublic — metadata alias remapping
# ---------------------------------------------------------------------------


def test_scan_public_remaps_scan_metadata_to_metadata_field() -> None:
    """
    The ORM column is named `metadata` in the DB but the Python attribute is
    `scan_metadata` (DeclarativeBase reserves `.metadata`). The schema must
    surface it as `metadata` to the wire and pull from `scan_metadata` in
    from_attributes mode.
    """
    from schemas.scan import ScanPublic

    now = datetime.now(tz=UTC)
    fake_row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        kind="source",
        status="queued",
        progress_percent=0,
        current_step=None,
        started_at=None,
        completed_at=None,
        error_message=None,
        requested_by_user_id=None,
        celery_task_id=None,
        scan_metadata={"git_ref": "main"},
        created_at=now,
        updated_at=now,
    )

    public = ScanPublic.model_validate(fake_row)
    assert public.metadata == {"git_ref": "main"}

    # When dumped with by_alias=True the wire field is `metadata`, not
    # `scan_metadata`.
    dumped = public.model_dump(by_alias=True)
    assert "metadata" in dumped
    assert "scan_metadata" not in dumped
    assert dumped["metadata"] == {"git_ref": "main"}


def test_scan_public_handles_missing_optional_fields() -> None:
    from schemas.scan import ScanPublic

    now = datetime.now(tz=UTC)
    fake_row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        kind="container",
        status="succeeded",
        progress_percent=100,
        current_step="finalizing",
        started_at=now,
        completed_at=now,
        error_message=None,
        requested_by_user_id=None,
        celery_task_id="task-123",
        scan_metadata={},
        created_at=now,
        updated_at=now,
    )

    public = ScanPublic.model_validate(fake_row)
    assert public.kind == "container"
    assert public.status == "succeeded"
    assert public.progress_percent == 100
    assert public.celery_task_id == "task-123"
