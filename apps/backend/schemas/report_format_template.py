# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Wire shapes for report formatting templates (N22).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import REPORT_COMPONENT_COLUMNS, REPORT_VULNERABILITY_COLUMNS

#: Mirrors the NOTICE template clamp's spirit — a stability bound on a plain
#: free-text field, not a design constraint.
_MAX_TEXT_LENGTH = 8_000
_MAX_LABEL_LENGTH = 200


class ReportFormatTemplateUpsertIn(BaseModel):
    """PUT body for one organization's report formatting defaults."""

    model_config = ConfigDict(extra="forbid")

    header_text: str | None = Field(
        default=None,
        max_length=_MAX_TEXT_LENGTH,
        description="Plain text printed under the report header. Null clears it.",
    )
    org_label: str | None = Field(
        default=None,
        max_length=_MAX_LABEL_LENGTH,
        description="Replaces the default brand text in the report header. Null clears it.",
    )
    vulnerability_columns: list[str] | None = Field(
        default=None,
        description=(
            "Non-empty subset of "
            f"{list(REPORT_VULNERABILITY_COLUMNS)}, in canonical order regardless of "
            "the order given. Null means every column renders (current behavior)."
        ),
    )
    component_columns: list[str] | None = Field(
        default=None,
        description=(
            f"Non-empty subset of {list(REPORT_COMPONENT_COLUMNS)}. Null means every "
            "column renders (current behavior)."
        ),
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> ReportFormatTemplateUpsertIn:
        if (
            self.header_text is None
            and self.org_label is None
            and self.vulnerability_columns is None
            and self.component_columns is None
        ):
            raise ValueError(
                "at least one of header_text, org_label, vulnerability_columns or "
                "component_columns is required"
            )
        return self

    @model_validator(mode="after")
    def _columns_are_known(self) -> ReportFormatTemplateUpsertIn:
        if self.vulnerability_columns is not None:
            _validate_column_subset(
                self.vulnerability_columns, REPORT_VULNERABILITY_COLUMNS, "vulnerability_columns"
            )
        if self.component_columns is not None:
            _validate_column_subset(
                self.component_columns, REPORT_COMPONENT_COLUMNS, "component_columns"
            )
        return self


def _validate_column_subset(
    columns: list[str], canonical: tuple[str, ...], field_name: str
) -> None:
    if not columns:
        raise ValueError(f"{field_name} may not be an empty list; omit it (null) instead")
    unknown = sorted(set(columns) - set(canonical))
    if unknown:
        raise ValueError(f"{field_name} contains unknown column(s): {unknown}")


class ReportFormatTemplateOut(BaseModel):
    """One organization's stored report formatting row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    header_text: str | None = None
    org_label: str | None = None
    vulnerability_columns: list[str] | None = None
    component_columns: list[str] | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "ReportFormatTemplateOut",
    "ReportFormatTemplateUpsertIn",
]
