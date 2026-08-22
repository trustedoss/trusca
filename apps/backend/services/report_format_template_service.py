# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Reading and writing report formatting templates (N22).

One row per organization. Writing is a super-admin decision the same way the
NOTICE template / gate / scan-schedule organization defaults are: this
formatting covers every project's report in the deployment, not one team's.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import Organization, ReportFormatTemplate
from schemas.report_format_template import ReportFormatTemplateUpsertIn

log = structlog.get_logger("services.report_format_template")


class ReportFormatTemplateError(Exception):
    """Base for the failures the router turns into Problem Details."""


class ReportFormatTemplateForbidden(ReportFormatTemplateError):
    """Caller may not write the organization's report formatting."""


class ReportFormatTemplateScopeNotFound(ReportFormatTemplateError):
    """The organization does not exist."""


def _is_super_admin(actor: CurrentUser) -> bool:
    return actor.is_superuser or actor.role == "super_admin"


async def get_template(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> ReportFormatTemplate | None:
    """The organization's report formatting row, or None if it has none.

    Called from the report endpoint itself (any team member's request), so
    this deliberately has no role gate of its own: reading the effective
    document is already authorized by the report endpoint's own project
    team-access check.
    """
    return (
        await session.execute(
            select(ReportFormatTemplate).where(
                ReportFormatTemplate.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()


async def upsert_template(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    payload: ReportFormatTemplateUpsertIn,
) -> ReportFormatTemplate:
    """Create or replace the organization's report formatting row."""
    exists = (
        await session.execute(select(Organization.id).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    if exists is None:
        raise ReportFormatTemplateScopeNotFound(f"organization {organization_id} not found")
    if not _is_super_admin(actor):
        raise ReportFormatTemplateForbidden(
            "only a super admin may write report formatting templates"
        )

    existing = await get_template(session, organization_id=organization_id)
    if existing is None:
        existing = ReportFormatTemplate(
            organization_id=organization_id,
            created_by_user_id=actor.id,
        )
        session.add(existing)

    existing.header_text = payload.header_text
    existing.org_label = payload.org_label
    existing.vulnerability_columns = payload.vulnerability_columns
    existing.component_columns = payload.component_columns
    await session.commit()
    await session.refresh(existing)
    return existing


async def delete_template(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
) -> bool:
    """Drop the organization's report formatting row.

    Returns whether a row was removed, so the router can answer 404 for an
    organization that never had one rather than reporting a delete that
    deleted nothing.
    """
    if not _is_super_admin(actor):
        raise ReportFormatTemplateForbidden(
            "only a super admin may write report formatting templates"
        )

    row = await get_template(session, organization_id=organization_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


__all__ = [
    "ReportFormatTemplateForbidden",
    "ReportFormatTemplateScopeNotFound",
    "delete_template",
    "get_template",
    "upsert_template",
]
