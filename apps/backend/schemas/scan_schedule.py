# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Wire shapes for scheduled scans (N18).

Unlike the gate policy's per-field fall-through, a schedule is one cohesive
unit: a row is either a real cadence or it is not, and a caller writing a
schedule states hour/cadence/timezone together rather than tuning one field
of an existing row at a time. ``is_active`` still exists on its own because
turning a schedule off and on is routine and should not require re-sending
the cadence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.scan_schedule import SCAN_SCHEDULE_CADENCE_VALUES

_KNOWN_ZONES = available_timezones()


class ScanScheduleUpsertIn(BaseModel):
    """PUT body for an organization or project scan schedule."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool = Field(
        default=True,
        description="Whether this row's schedule fires. False opts a project out "
        "of an organization default without deleting the row.",
    )
    cadence: str | None = Field(
        default=None,
        description="'daily' or 'weekly'. Null means this row decides nothing yet.",
    )
    hour: int | None = Field(
        default=None,
        ge=0,
        le=23,
        description="Local hour-of-day (0-23) the schedule fires, read in `timezone`.",
    )
    day_of_week: int | None = Field(
        default=None,
        ge=0,
        le=6,
        description="0=Monday..6=Sunday. Required for 'weekly', forbidden for 'daily'.",
    )
    timezone: str = Field(
        default="UTC",
        description="IANA zone name the hour/day-of-week are read against.",
    )

    @model_validator(mode="after")
    def _consistent_schedule(self) -> ScanScheduleUpsertIn:
        if self.cadence is not None and self.cadence not in SCAN_SCHEDULE_CADENCE_VALUES:
            raise ValueError(
                f"cadence must be one of {SCAN_SCHEDULE_CADENCE_VALUES} or null"
            )
        if self.cadence is not None and self.hour is None:
            raise ValueError("hour is required when cadence is set")
        if self.cadence == "weekly" and self.day_of_week is None:
            raise ValueError("day_of_week is required when cadence is 'weekly'")
        if self.cadence == "daily" and self.day_of_week is not None:
            raise ValueError("day_of_week must be null when cadence is 'daily'")
        if self.timezone not in _KNOWN_ZONES:
            raise ValueError(f"unknown IANA timezone: {self.timezone!r}")
        return self


class ScanScheduleOut(BaseModel):
    """One stored schedule row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID | None = Field(
        default=None, description="Null for the organization default."
    )
    is_active: bool
    cadence: str | None = None
    hour: int | None = None
    day_of_week: int | None = None
    timezone: str
    last_triggered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EffectiveScanScheduleOut(BaseModel):
    """What will actually fire for a project, after the fall-through.

    Whole-row, not per-field: a project row (active or not) always wins over
    the organization default, so ``source`` names which row (if either)
    supplied the answer rather than which field.
    """

    project_id: uuid.UUID
    is_active: bool
    cadence: str | None = None
    hour: int | None = None
    day_of_week: int | None = None
    timezone: str | None = None
    source: str = Field(
        description="'project', 'organization', or 'none' when nothing schedules this project."
    )


__all__ = [
    "EffectiveScanScheduleOut",
    "ScanScheduleOut",
    "ScanScheduleUpsertIn",
]
