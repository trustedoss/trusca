# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Reading and writing NOTICE boilerplate templates (N21).

One organization, one row per format. Writing is a super-admin decision the
same way the gate/scan-schedule organization default is: this boilerplate
covers the whole deployment, not one team, so the grade that answers for the
deployment is the one that sets it.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import NoticeTemplate, Organization
from schemas.notice_template import NoticeTemplateUpsertIn

log = structlog.get_logger("services.notice_template")


class NoticeTemplateError(Exception):
    """Base for the failures the router turns into Problem Details."""


class NoticeTemplateForbidden(NoticeTemplateError):
    """Caller may not write the organization's template."""


class NoticeTemplateScopeNotFound(NoticeTemplateError):
    """The organization does not exist, or the template row does not."""


def _is_super_admin(actor: CurrentUser) -> bool:
    return actor.is_superuser or actor.role == "super_admin"


async def get_template(
    session: AsyncSession, *, organization_id: uuid.UUID, format: str
) -> NoticeTemplate | None:
    """The organization's template for ``format``, or None if it has none.

    Called from the NOTICE renderer itself (any team member's request), so
    this deliberately has no role gate of its own — reading the effective
    document is already authorized by ``generate_notice``'s own project
    team-access check.
    """
    return (
        await session.execute(
            select(NoticeTemplate).where(
                NoticeTemplate.organization_id == organization_id,
                NoticeTemplate.format == format,
            )
        )
    ).scalar_one_or_none()


async def upsert_template(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    format: str,
    payload: NoticeTemplateUpsertIn,
) -> NoticeTemplate:
    """Create or replace the organization's template for one format."""
    exists = (
        await session.execute(select(Organization.id).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    if exists is None:
        raise NoticeTemplateScopeNotFound(f"organization {organization_id} not found")
    if not _is_super_admin(actor):
        raise NoticeTemplateForbidden("only a super admin may write NOTICE templates")

    existing = await get_template(session, organization_id=organization_id, format=format)
    if existing is None:
        existing = NoticeTemplate(
            organization_id=organization_id,
            format=format,
            created_by_user_id=actor.id,
        )
        session.add(existing)

    existing.preface = payload.preface
    existing.footer = payload.footer
    await session.commit()
    await session.refresh(existing)
    return existing


async def delete_template(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    format: str,
) -> bool:
    """Drop the organization's template for one format.

    Returns whether a row was removed, so the router can answer 404 for a
    format that never had one rather than reporting a delete that deleted
    nothing.
    """
    if not _is_super_admin(actor):
        raise NoticeTemplateForbidden("only a super admin may write NOTICE templates")

    row = await get_template(session, organization_id=organization_id, format=format)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


__all__ = [
    "NoticeTemplateForbidden",
    "NoticeTemplateScopeNotFound",
    "delete_template",
    "get_template",
    "upsert_template",
]
