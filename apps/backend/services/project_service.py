"""
Project domain services — Phase 2 PR #7.

The router (`api/v1/projects.py`) is a thin shell: it only translates HTTP
into service calls and turns these domain exceptions into RFC 7807. All DB
I/O — including IDOR / RBAC enforcement — lives here.

Cross-team data isolation is the most important contract in this module:
  - `list_projects` always restricts the returned rows to the actor's team
    set unless the actor is a super_admin.
  - `get_project`, `update_project`, `archive_project` re-check team membership
    at read time. Even if the caller crafts a UUID belonging to another team,
    the service raises `ProjectForbidden` (mapped to 403 in the router).

Phase 3+ TODO: when organization-wide visibility is enabled in the API,
`list_projects` will also include projects with visibility='organization'
that share the actor's organization_id. For PR #7 only visibility='team' is
writable so the additional clause is intentionally absent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import bind_audit_team as _bind_audit_team
from core.crypto import SecretEncryptionError, encrypt_secret
from core.pii_mask import url_userinfo_is_redacted
from core.security import CurrentUser
from models import Project
from schemas.scan import ProjectCreate, ProjectUpdate

log = structlog.get_logger("project.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ProjectError(Exception):
    """Base class for project-domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Project Error"


class ProjectNotFound(ProjectError):
    status_code = 404
    title = "Project Not Found"


class ProjectSlugConflict(ProjectError):
    status_code = 409
    title = "Project Slug Conflict"


class ProjectForbidden(ProjectError):
    status_code = 403
    title = "Forbidden"


class ProjectCredentialEncryptionError(ProjectError):
    """The git credential could not be encrypted (misconfigured encryption key).

    Maps to 503: this is an operational / deployment misconfiguration
    (``GITHUB_APP_ENCRYPTION_KEY`` unset in prod, or malformed), not a client
    error. The RFC 7807 detail is credential-free — it never echoes the plaintext.
    """

    status_code = 503
    title = "Credential Encryption Unavailable"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from core.authz import assert_team_access  # noqa: E402

# All cross-team guards in this module flow through `assert_team_access`
# (chore PR #5) so the `authz.cross_team_attempt` log shape is centralized.


def _can_write_project(actor: CurrentUser, project: Project) -> bool:
    """
    Mutating ops require role >= team_admin within the project's *own* team.

    Cross-team role escalation guard (CWE-863): we look up the actor's role
    in `actor.team_roles[project.team_id]`, not in `actor.role`. The latter
    is the highest role across all memberships and would let a user who is
    team_admin in team_a and developer in team_b mutate team_b projects.
    super_admin still bypasses the team check entirely.
    """
    if actor.is_superuser or actor.role == "super_admin":
        return True
    role_in_team = actor.team_roles.get(project.team_id)
    return role_in_team == "team_admin"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_project(
    session: AsyncSession,
    *,
    payload: ProjectCreate,
    actor: CurrentUser,
) -> Project:
    """
    Insert a new project owned by `payload.team_id`.

    - 403 if the actor is not a member of the target team (and not super_admin).
    - 409 if (team_id, slug) already exists — caught from the unique constraint.
    """
    assert_team_access(
        actor,
        payload.team_id,
        log=log,
        resource="project",
        resource_id=str(payload.team_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {payload.team_id}",
        ),
    )

    _bind_audit_team(payload.team_id)

    # Capture inputs into locals so the except branch never has to read off
    # the ORM instance (which is expired after rollback on the async engine
    # and would trip MissingGreenlet on attribute access).
    target_slug = payload.slug

    project = Project(
        team_id=payload.team_id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        git_url=payload.git_url,
        default_branch=payload.default_branch,
        visibility=payload.visibility,
        created_by_user_id=actor.id,
    )
    session.add(project)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # The unique constraint is uq_projects_team_slug. If a future
        # constraint is added we still want to surface a clean 409 — leave
        # the message generic.
        raise ProjectSlugConflict(
            f"a project with slug {target_slug!r} already exists in this team",
        ) from exc

    await session.refresh(project)
    log.info(
        "project_created",
        project_id=str(project.id),
        team_id=str(project.team_id),
        slug=project.slug,
    )
    return project


# ---------------------------------------------------------------------------
# List (paginated, team-scoped)
# ---------------------------------------------------------------------------


async def list_projects(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    team_id: uuid.UUID | None = None,
    include_archived: bool = False,
    q: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Project], int]:
    """
    Return (rows, total) for the projects visible to `actor`.

    - super_admin sees every team unless `team_id` is supplied.
    - everyone else is hard-clamped to `actor.team_ids`. If `team_id` is
      supplied it must be in `actor.team_ids`, otherwise 403.
    - Phase 3+ TODO: include organization-wide projects sharing the actor's
      organization. Not enabled in PR #7.
    """
    page = max(page, 1)
    size = max(min(size, 100), 1)

    is_super = actor.is_superuser or actor.role == "super_admin"

    # Build the WHERE clause for team scoping.
    if team_id is not None:
        if not is_super and team_id not in actor.team_ids:
            raise ProjectForbidden(
                f"actor is not a member of team {team_id}",
            )
        scoped_team_ids: list[uuid.UUID] = [team_id]
    elif is_super:
        scoped_team_ids = []  # no team filter — super_admin sees all
    else:
        scoped_team_ids = list(actor.team_ids)
        if not scoped_team_ids:
            return [], 0

    base = select(Project)
    count_base = select(func.count()).select_from(Project)

    if scoped_team_ids:
        base = base.where(Project.team_id.in_(scoped_team_ids))
        count_base = count_base.where(Project.team_id.in_(scoped_team_ids))

    if not include_archived:
        base = base.where(Project.archived_at.is_(None))
        count_base = count_base.where(Project.archived_at.is_(None))

    if q:
        # Substring match on name OR exact-prefix on slug. Cheap because of
        # ix_projects_team_archived (team_id leading) — Postgres can hash join
        # the ILIKE on the smaller filtered set.
        like = f"%{q.strip()}%"
        base = base.where(
            or_(Project.name.ilike(like), Project.slug.ilike(like)),
        )
        count_base = count_base.where(
            or_(Project.name.ilike(like), Project.slug.ilike(like)),
        )

    total_result = await session.execute(count_base)
    total = int(total_result.scalar_one())

    rows_stmt = (
        base.order_by(Project.updated_at.desc(), Project.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    rows_result = await session.execute(rows_stmt)
    rows = list(rows_result.scalars().all())
    return rows, total


# ---------------------------------------------------------------------------
# Get / Update / Archive
# ---------------------------------------------------------------------------


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")
    return project


async def get_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
) -> Project:
    """Return the project, or raise ProjectForbidden / ProjectNotFound."""
    project = await _load_project(session, project_id)
    # IDOR guard: returning 404 vs 403 leaks existence — we deliberately
    # raise 403 because team membership is itself a privileged signal in
    # this product (collaborators know who is on which team). 404-on-
    # forbidden would be safer if existence were secret; it is not here.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}",
        ),
    )
    return project


async def update_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    actor: CurrentUser,
) -> Project:
    """Patch updatable fields. Requires role >= team_admin in the target team."""
    project = await _load_project(session, project_id)
    if not _can_write_project(actor, project):
        raise ProjectForbidden("requires role >= team_admin within the project's team")

    _bind_audit_team(project.team_id)

    # Pydantic v2: exclude_unset means we only touch fields the caller sent.
    # That matters for `description`/`git_url`/`default_branch` whose `None`
    # is a legitimate "clear this field" value — distinguishing "unset" from
    # "explicit null" is what gives us PATCH semantics.
    updates = payload.model_dump(exclude_unset=True)

    # C-2 round-trip guard — a git_url read back from the API has its userinfo
    # redacted to ``https://***@host/...``. The settings form prefills git_url
    # from that masked read, so an unchanged save re-submits the mask. Persisting
    # it would overwrite the real inline credential with ``***@`` and break future
    # clones. Drop the field so the stored value is preserved; a genuine edit
    # supplies a fresh URL (no ``***`` userinfo) and flows through normally.
    incoming_git_url = updates.get("git_url")
    if isinstance(incoming_git_url, str) and url_userinfo_is_redacted(incoming_git_url):
        updates.pop("git_url")
        log.info("project_update_ignored_redacted_git_url", project_id=str(project.id))

    # Feature #18 Part B — the git credential is handled SEPARATELY from the
    # generic setattr loop because (a) the inbound `git_credential` /
    # `clear_git_credential` are NOT ORM columns (the column is
    # `git_credential_encrypted`), and (b) the plaintext must be encrypted before
    # it touches the row and must NEVER be logged. We pop both keys out of
    # `updates` so the generic loop below never sees them.
    raw_credential = updates.pop("git_credential", None)
    clear_credential = updates.pop("clear_git_credential", False)

    # `audit_fields` is the credential-free list we log + record (we log the
    # *fact* a credential changed, never the value).
    audit_fields = list(updates.keys())
    credential_changed = False

    for field, value in updates.items():
        setattr(project, field, value)

    # Credential set/rotate takes precedence is impossible here — the schema's
    # model_validator already rejected "set AND clear" in one request, so at most
    # one of these branches runs.
    if isinstance(raw_credential, str) and raw_credential.strip() != "":
        # Encrypt the plaintext BEFORE assigning to the row. encrypt_secret never
        # logs the plaintext; we never log `raw_credential` here either. On a
        # misconfigured key we surface a clean 503 with NO plaintext in the message.
        try:
            project.git_credential_encrypted = encrypt_secret(raw_credential)
        except SecretEncryptionError as exc:
            # The exception message from core.crypto is credential-free (it talks
            # about the KEY, never the plaintext) — but we deliberately do NOT
            # interpolate the secret regardless. Log without any credential bytes.
            log.error(
                "project_credential_encrypt_failed",
                project_id=str(project.id),
                error=str(exc),
            )
            raise ProjectCredentialEncryptionError(
                "the git credential could not be encrypted; the deployment's "
                "credential encryption key is unset or misconfigured",
            ) from exc
        credential_changed = True
        audit_fields.append("git_credential")
    elif clear_credential:
        project.git_credential_encrypted = None
        credential_changed = True
        audit_fields.append("git_credential")

    project.updated_at = datetime.now(tz=UTC)

    try:
        await session.commit()
    except IntegrityError as exc:
        # Currently no updatable field is unique-constrained, but `name` could
        # become so in a future revision. Translate any constraint violation
        # to 409 so callers get a stable error envelope.
        await session.rollback()
        raise ProjectSlugConflict("project update violated a uniqueness constraint") from exc

    await session.refresh(project)
    # NEVER log the plaintext or the ciphertext — only that a credential changed.
    # `fields` carries the non-secret field names; `credential_changed` is a bool.
    log.info(
        "project_updated",
        project_id=str(project.id),
        fields=audit_fields,
        credential_changed=credential_changed,
    )
    return project


async def archive_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
) -> Project:
    """Soft-delete: stamps archived_at. Idempotent on already-archived rows."""
    project = await _load_project(session, project_id)
    if not _can_write_project(actor, project):
        raise ProjectForbidden("requires role >= team_admin within the project's team")

    _bind_audit_team(project.team_id)

    if project.archived_at is None:
        project.archived_at = datetime.now(tz=UTC)
        project.updated_at = project.archived_at
        await session.commit()
        await session.refresh(project)
        log.info("project_archived", project_id=str(project.id))
    return project


__all__ = [
    "ProjectCredentialEncryptionError",
    "ProjectError",
    "ProjectForbidden",
    "ProjectNotFound",
    "ProjectSlugConflict",
    "archive_project",
    "create_project",
    "get_project",
    "list_projects",
    "update_project",
]
