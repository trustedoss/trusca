# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Wire shapes for NOTICE boilerplate templates (N21).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Plain text is capped generously above any real letterhead/disclaimer,
#: this is a stability bound, not a design constraint, and mirrors the
#: obligation-text clamp's spirit rather than its exact number.
_MAX_TEMPLATE_LENGTH = 8_000


class NoticeTemplateUpsertIn(BaseModel):
    """PUT body for one organization's per-format NOTICE template."""

    model_config = ConfigDict(extra="forbid")

    preface: str | None = Field(
        default=None,
        max_length=_MAX_TEMPLATE_LENGTH,
        description="Plain text printed before the license list. Null clears it.",
    )
    footer: str | None = Field(
        default=None,
        max_length=_MAX_TEMPLATE_LENGTH,
        description="Plain text printed after the license list. Null clears it.",
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> NoticeTemplateUpsertIn:
        if self.preface is None and self.footer is None:
            raise ValueError("at least one of preface or footer is required")
        return self


class NoticeTemplateOut(BaseModel):
    """One stored template row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    format: str
    preface: str | None = None
    footer: str | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "NoticeTemplateOut",
    "NoticeTemplateUpsertIn",
]
