# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Wire shapes for build-gate policy.

Every field is optional and defaults to ``None``, mirroring the columns. A PUT
that omits a field stores NULL for it, which means "not decided at this scope"
and lets the value keep falling through to the organization or the deployment.
That is deliberately different from "the caller forgot": a client that wants to
stop overriding a field sends it as null rather than deleting the whole policy,
and a client that wants to keep it sends it back.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GatePolicyUpsertIn(BaseModel):
    """PUT body for an organization or team gate policy."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        max_length=120,
        description="Label for the policy. The scope is its identity; this is for the UI.",
    )
    epss_threshold: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Block when an open finding's exploit-prediction score reaches this. "
            "Null leaves the condition off, which is the behaviour with no policy."
        ),
    )
    reachable_critical_only: bool | None = Field(
        default=None,
        description=(
            "Count only criticals an analyser proved reachable. This can only "
            "shrink the blocking set, never widen it, and it applies only to "
            "scans that were actually analysed."
        ),
    )
    malicious_blocks: bool | None = Field(
        default=None,
        description=(
            "Whether a package the malicious snapshot flags blocks the build. "
            "Unlike the other two this is on by default, so a policy row only "
            "ever turns it off deliberately."
        ),
    )


class GatePolicyOut(BaseModel):
    """One stored policy row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    team_id: uuid.UUID | None = Field(
        default=None, description="Null for the organization default."
    )
    name: str | None = None
    epss_threshold: float | None = None
    reachable_critical_only: bool | None = None
    malicious_blocks: bool | None = None
    created_at: datetime
    updated_at: datetime


class EffectiveGatePolicyOut(BaseModel):
    """What a project actually evaluates against, after the fall-through.

    Separate from :class:`GatePolicyOut` because it is not a row: a field here
    may come from the team, the organization, or neither. ``sources`` says
    which, so an operator reading "EPSS 0.5" can tell whether their team set it
    or inherited it, which is the first question anyone asks of an effective
    value.
    """

    project_id: uuid.UUID
    epss_threshold: float | None = None
    reachable_critical_only: bool | None = None
    malicious_blocks: bool | None = None
    sources: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Field name to the scope that supplied it: 'team', 'organization', "
            "or 'deployment' when no policy decided it."
        ),
    )
