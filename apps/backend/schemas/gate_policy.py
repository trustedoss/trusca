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

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Statuses an organization may put behind a second person. Restricted to the
#: ones that end the obligation, because those are what "accepting risk" means;
#: gating the working states (new, analyzing) would stop triage rather than
#: stopping a decision, and nobody asked for that.
APPROVABLE_STATUSES: frozenset[str] = frozenset(
    {"not_affected", "false_positive", "fixed", "suppressed"}
)


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
    approval_required_statuses: list[str] | None = Field(
        default=None,
        description=(
            "Finding statuses one person may not reach alone. Reaching one of "
            "these opens a request that somebody else decides. Null or empty "
            "means every transition stays a single action."
        ),
    )

    @field_validator("approval_required_statuses")
    @classmethod
    def _known_statuses_only(cls, value: list[str] | None) -> list[str] | None:
        """Reject names that would look configured and do nothing.

        A misspelt status stored as-is is the worst outcome available here: the
        policy page shows a control in place, and every transition it was meant
        to catch goes through unremarked.
        """
        if value is None:
            return None
        unknown = sorted(set(value) - APPROVABLE_STATUSES)
        if unknown:
            raise ValueError(
                f"not statuses that can require approval: {', '.join(unknown)}; "
                f"choose from {', '.join(sorted(APPROVABLE_STATUSES))}"
            )
        # Order carries no meaning and duplicates are not a second requirement.
        return sorted(set(value))


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
    approval_required_statuses: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class EpssAvailabilityOut(BaseModel):
    """Whether this deployment has EPSS data behind any threshold set on it.

    Deployment-scoped on purpose, and separate from the gate result for the
    same reason: the gate answers "did the EPSS axis judge anything on THIS
    scan", which a policy screen cannot ask because it is not looking at one.
    An administrator setting a threshold needs to know whether the deployment
    collects EPSS at all, before the next scan runs.
    """

    available: bool = Field(
        description=(
            "False when the daily sync is off, has not succeeded recently, or "
            "has never written a score. A threshold set while this is false "
            "decides nothing, and the builds it was meant to block pass."
        ),
    )
    refresh_enabled: bool = Field(
        description=(
            "Whether `EPSS_REFRESH_ENABLED` is on. Off is the default, so a "
            "threshold on a fresh install has nothing to evaluate until an "
            "operator turns the sync on or points `EPSS_FEED_URL` at a mirror."
        ),
    )
    scored_cves: int = Field(
        ge=0,
        description="CVEs in this deployment's catalog carrying an EPSS score.",
    )
    last_synced_at: datetime | None = Field(
        default=None,
        description=(
            "When the sync last completed successfully; `null` when it never "
            "has. A timestamp several days old means recent ticks have not "
            "landed, so the scores are drifting even though values exist."
        ),
    )


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
    approval_required_statuses: list[str] = Field(
        default_factory=list,
        description=(
            "Statuses this project may not reach without a second person. "
            "The organization's list plus anything the team added: a team may "
            "be stricter than its organization, never looser."
        ),
    )
    epss_data_available: bool = Field(
        default=True,
        description=(
            "Whether this deployment actually has EPSS data behind the "
            "threshold above. False when the daily sync is switched off, has "
            "not succeeded recently, or has never written a score, in which "
            "case a threshold set here decides nothing and the builds it was "
            "meant to block pass. The fields below say which of those it is."
        ),
    )
    epss_refresh_enabled: bool = Field(
        default=False,
        description=(
            "Whether `EPSS_REFRESH_ENABLED` is on for this deployment. Off is "
            "the default, so a threshold configured on a fresh install has "
            "nothing to evaluate until an operator turns the sync on or "
            "points `EPSS_FEED_URL` at an internal mirror."
        ),
    )
    epss_scored_cves: int = Field(
        default=0,
        ge=0,
        description="CVEs in this deployment's catalog that carry an EPSS score.",
    )
    epss_last_synced_at: datetime | None = Field(
        default=None,
        description=(
            "When the EPSS sync last completed successfully. `null` when it "
            "has never run. A timestamp several days old means recent ticks "
            "have not landed, so the scores are drifting even though values "
            "exist."
        ),
    )
    sources: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Field name to the scope that supplied it: 'team', 'organization', "
            "or 'deployment' when no policy decided it. "
            "``approval_required_statuses`` may also read 'team+organization', "
            "because that field is a union rather than a fall-through."
        ),
    )
