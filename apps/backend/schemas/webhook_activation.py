# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Issuing a project's webhook shared secret.

Activation used to be an operator running an UPDATE by hand, because the
column held plaintext and SQL could produce it. It holds Fernet ciphertext
now, which SQL cannot produce, so issuing the secret is a route.

The value is surfaced once, in the response to the request that creates it,
and never again. That is the same contract ``APIKeyCreateOut.raw_key`` has,
and for the same reason: the deployment does not need to be able to read it
back, only to verify against it, and a value that can be read back is a value
that leaks through every screen and log that ever displays it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

WebhookProvider = Literal["github", "gitlab"]


class WebhookSecretIssueIn(BaseModel):
    """Request body for issuing (or re-issuing) a project's webhook secret."""

    model_config = ConfigDict(extra="forbid")

    provider: WebhookProvider = Field(
        description=(
            "Which SCM will send the deliveries. It decides the header schema "
            "the gateway applies: GitHub signs the body and sends "
            "X-Hub-Signature-256, GitLab sends the secret itself as "
            "X-Gitlab-Token. A project configured for one and receiving from "
            "the other has every delivery refused."
        )
    )


class WebhookSecretIssueOut(BaseModel):
    """Response carrying the plaintext, exactly once."""

    project_id: UUID
    provider: WebhookProvider
    issued_at: datetime
    replaced_existing: bool = Field(
        description=(
            "True when this replaced a secret the project already had. The "
            "old value stops being accepted immediately, so deliveries fail "
            "until the new one is pasted into the SCM. Stated in the response "
            "rather than left to be discovered from refused deliveries."
        )
    )
    secret: str = Field(
        ...,
        description=(
            "The shared secret, to paste into the SCM's webhook settings. "
            "Returned exactly once: it is stored encrypted and is never "
            "surfaced again. Losing it means issuing a new one."
        ),
    )


class WebhookStatusOut(BaseModel):
    """Whether a project's webhook is active, without the secret.

    Separate from the issue response so that the ordinary way to look at a
    project cannot return credential bytes. ``configured`` answers what a
    screen needs; it never answers what the value is.
    """

    project_id: UUID
    configured: bool
    provider: WebhookProvider | None
