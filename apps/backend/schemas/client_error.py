# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Pydantic schema for the frontend crash-report intake (#423).

One shape, `ClientErrorReportIn`, for `POST /v1/client-errors`. Every field
is capped: this is free text a browser sends about itself, not something
the caller has any incentive to keep small, and an oversized log line is a
cheap way to degrade whatever aggregates these.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ClientErrorReportIn(BaseModel):
    """One `ErrorBoundary` catch, as the browser saw it."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000, description="`error.message`.")
    stack: str | None = Field(
        default=None, max_length=8000, description="`error.stack`, if the browser set one."
    )
    component_stack: str | None = Field(
        default=None,
        max_length=8000,
        description="React's `errorInfo.componentStack`: which component tree threw.",
    )
    url: str = Field(
        min_length=1,
        max_length=2000,
        description="`window.location.href` at the moment of the crash.",
    )


__all__ = ["ClientErrorReportIn"]
