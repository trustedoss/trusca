"""
Pydantic schemas for license policy management — v2.2 (Track C — c1).

Public shapes:
  - LicensePolicyUpsertIn   — request body for PUT /v1/license-policies/{teams|org}/{id}
  - LicensePolicyOut        — response (ORM-derived, from_attributes)
  - LicensePolicyListPage   — paginated list wrapper
  - PolicyCategory          — Literal alias for the 3-value gate posture set
  - CompoundStrategy        — Literal alias for compound-operator resolution
  - LicenseException        — one entry in ``license_exceptions``

Design notes / security boundary (this is untrusted input — see MEMORY
feedback_adversarial_input_parametrize):
  - The JSONB columns accept caller-supplied maps/arrays. JSONB cannot CHECK
    individual values at the DB layer, so ALL value validation lives here:
      * ``category_overrides`` keys are length-bounded SPDX-ish identifiers with
        no control characters; values are constrained to the ``PolicyCategory``
        literal set.
      * the override map is size-bounded (``_MAX_OVERRIDES``) so a hostile 10k+
        entry payload is a clean 422, never a 500 / OOM.
      * ``license_exceptions`` is size-bounded (``_MAX_EXCEPTIONS``); each entry
        is a strict model (extra keys forbidden, ``spdx_id`` + ``reason``
        required, ``expires_at`` an optional aware datetime).
      * ``unknown_license_category`` is the literal posture set (rejects e.g.
        "unknown", "ALLOWED", arbitrary strings).
      * ``compound_operator_strategy`` keys are the closed operator set and
        values the closed strategy set.
  - A bad value fails fast with a 422 RFC 7807 envelope (FastAPI's
    RequestValidationError handler), NOT a 500.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# The 3-value gate posture set. ``unknown`` is intentionally NOT a member — a
# policy assigns a concrete posture, it never relabels a license to "unknown".
PolicyCategory = Literal["allowed", "conditional", "forbidden"]

# Compound-operator resolution strategy (c2 consumes this).
CompoundStrategy = Literal["most_restrictive", "least_restrictive"]

# Closed operator set for ``compound_operator_strategy`` keys.
CompoundOperator = Literal["AND", "OR", "WITH"]

# ---------------------------------------------------------------------------
# Bounds (adversarial-input guards)
# ---------------------------------------------------------------------------

# Real-world override maps are dozens of entries. 2000 is comfortably above any
# legitimate use and well below a payload that would stress JSONB / memory.
_MAX_OVERRIDES = 2000
_MAX_EXCEPTIONS = 2000
# SPDX short identifiers top out around 60 chars (e.g. compound LicenseRef-*);
# the DB column for License.spdx_id is String(64). Bound keys to 128 to allow a
# little headroom for purl-ish exception ids while still rejecting megabyte keys.
_MAX_SPDX_LEN = 128
_MAX_REASON_LEN = 1000
_MAX_PURL_LEN = 1024
_MAX_NAME_LEN = 120


def _default_compound_strategy() -> dict[CompoundOperator, CompoundStrategy]:
    """The conservative default compound-operator resolution (c2 reads this)."""
    return {
        "AND": "most_restrictive",
        "OR": "least_restrictive",
        "WITH": "most_restrictive",
    }


def _has_control_chars(value: str) -> bool:
    """True if *value* contains an ASCII control char (incl. NUL, CR, LF, TAB)."""
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _validate_spdx_key(key: str) -> str:
    """Validate a single SPDX-ish identifier used as a map key / exception id."""
    if not isinstance(key, str):
        raise ValueError("SPDX identifier must be a string")
    if not key.strip():
        raise ValueError("SPDX identifier must be non-empty")
    if len(key) > _MAX_SPDX_LEN:
        raise ValueError(f"SPDX identifier exceeds {_MAX_SPDX_LEN} characters")
    if _has_control_chars(key):
        raise ValueError("SPDX identifier must not contain control characters")
    return key


# ---------------------------------------------------------------------------
# LicenseException
# ---------------------------------------------------------------------------


class LicenseException(BaseModel):
    """One explicit allow-regardless-of-category waiver.

    ``spdx_id`` + ``reason`` are required. ``expires_at`` (optional) lets c2
    treat an expired waiver as absent. ``component_purl`` (optional) scopes the
    waiver to a single component; absent → applies to any component carrying
    ``spdx_id``. Extra keys are rejected so a typo cannot silently smuggle an
    un-validated field through the JSONB column.
    """

    model_config = ConfigDict(extra="forbid")

    spdx_id: str = Field(..., min_length=1, max_length=_MAX_SPDX_LEN)
    reason: str = Field(..., min_length=1, max_length=_MAX_REASON_LEN)
    expires_at: datetime | None = None
    component_purl: str | None = Field(default=None, max_length=_MAX_PURL_LEN)

    @field_validator("spdx_id")
    @classmethod
    def _check_spdx(cls, v: str) -> str:
        return _validate_spdx_key(v)

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, v: str) -> str:
        if _has_control_chars(v.replace("\n", "").replace("\r", "").replace("\t", "")):
            # Allow newlines/tabs in free-text reason; reject other control chars.
            raise ValueError("reason must not contain control characters")
        return v

    @field_validator("component_purl")
    @classmethod
    def _check_purl(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.strip():
            raise ValueError("component_purl must be non-empty when provided")
        if _has_control_chars(v):
            raise ValueError("component_purl must not contain control characters")
        return v


# ---------------------------------------------------------------------------
# Upsert input
# ---------------------------------------------------------------------------


class LicensePolicyUpsertIn(BaseModel):
    """Request body for PUT (upsert) of a team or org license policy.

    All fields are optional with sensible defaults so a minimal ``{}`` body
    creates an empty (effectively no-op) policy. Strict validation rejects
    oversized / malformed maps with a 422 (never a 500).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=_MAX_NAME_LEN)
    category_overrides: dict[str, PolicyCategory] = Field(default_factory=dict)
    license_exceptions: list[LicenseException] = Field(default_factory=list)
    unknown_license_category: PolicyCategory = "conditional"
    compound_operator_strategy: dict[CompoundOperator, CompoundStrategy] = Field(
        default_factory=lambda: _default_compound_strategy()
    )
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if _has_control_chars(v):
            raise ValueError("name must not contain control characters")
        return v

    @field_validator("category_overrides")
    @classmethod
    def _check_overrides(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > _MAX_OVERRIDES:
            raise ValueError(f"category_overrides exceeds {_MAX_OVERRIDES} entries")
        for key in v:
            _validate_spdx_key(key)
        return v

    @field_validator("license_exceptions")
    @classmethod
    def _check_exceptions(cls, v: list[LicenseException]) -> list[LicenseException]:
        if len(v) > _MAX_EXCEPTIONS:
            raise ValueError(f"license_exceptions exceeds {_MAX_EXCEPTIONS} entries")
        return v

    @model_validator(mode="after")
    def _check_compound_keys(self) -> LicensePolicyUpsertIn:
        # Pydantic already constrains keys to CompoundOperator + values to
        # CompoundStrategy via the typed dict. This guards the (legal) case of a
        # partial map — fill any missing operator with the conservative default
        # so c2 always sees all three keys.
        merged: dict[CompoundOperator, CompoundStrategy] = _default_compound_strategy()
        merged.update(self.compound_operator_strategy)
        object.__setattr__(self, "compound_operator_strategy", merged)
        return self


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class LicensePolicyOut(BaseModel):
    """ORM-derived response shape for a single license policy."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    team_id: UUID | None
    name: str | None
    category_overrides: dict[str, str]
    license_exceptions: list[dict[str, object]]
    unknown_license_category: str
    compound_operator_strategy: dict[str, str]
    enabled: bool
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class LicensePolicyListPage(BaseModel):
    """Paginated list of license policies."""

    items: list[LicensePolicyOut]
    total: int
    page: int
    page_size: int


__all__ = [
    "CompoundOperator",
    "CompoundStrategy",
    "LicenseException",
    "LicensePolicyListPage",
    "LicensePolicyOut",
    "LicensePolicyUpsertIn",
    "PolicyCategory",
]
