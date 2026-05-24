"""
VEX import (consume) response schemas — v2.1 Track A (A2).

The import endpoint (``POST /v1/projects/{project_id}/vex/import``) accepts an
uploaded VEX document (OpenVEX or CycloneDX VEX, format auto-detected) and
auto-transitions matching findings, suppressing triage noise. It is the inverse
of A1's export; export↔import must round-trip stably (re-importing an exported
document is a no-op at the status level).

These schemas describe the JSON *response* only — the request is a multipart
file upload, so there is no request body model. The response is a summary the
UI renders as a result panel:

    {
      "matched": 12,        # statements that resolved to ≥1 finding
      "applied": 9,         # findings whose status was actually changed
      "skipped": 5,         # statements/findings deliberately not applied
      "errors": [ ... ]     # per-statement structured skip/error reasons
    }

``matched``/``applied``/``skipped`` are statement-and-finding counts (a single
statement can fan out to multiple findings when one CVE affects multiple
component versions). ``errors`` carries a structured reason per skipped or
failed statement so the analyst can see *why* a row was not applied (unknown
vuln, unknown purl, illegal transition, already at target, …) without parsing
prose.

A *whole-document* parse failure (broken JSON, unrecognised format, oversized
body) does NOT return this summary — it is an RFC 7807 ``application/problem+
json`` 422/413/415 raised by the router. This summary is only returned once the
document parsed and individual statements could be evaluated.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Why a statement was skipped or failed. Closed set so the UI can localize each
# reason and the test suite can assert on a stable vocabulary. Mirrors the
# ``VEXSkipReason`` literals in ``services/vex_import.py``.
VEXImportSkipReason = Literal[
    "unknown_vulnerability",
    "unknown_component",
    "ambiguous_match",
    "unmapped_status",
    "illegal_transition",
    "already_at_target",
    "forbidden_transition",
    "malformed_statement",
]


class VEXImportItemError(BaseModel):
    """One structured skip/error row in the import summary.

    Identifies the offending statement by its VEX coordinates (vulnerability id
    + product/affects ref) so the analyst can locate it in the source document,
    plus a closed-vocabulary ``reason`` and a human-readable ``detail``.
    """

    model_config = ConfigDict(extra="forbid")

    vulnerability: str | None = Field(
        default=None,
        description="CVE/GHSA/OSV id the statement targeted (None if absent).",
    )
    product: str | None = Field(
        default=None,
        description="Product/affects ref (purl) the statement targeted.",
    )
    reason: VEXImportSkipReason = Field(
        description="Closed-vocabulary reason the statement was not applied.",
    )
    detail: str = Field(
        description="Human-readable explanation for the skip/error.",
    )


class VEXImportSummary(BaseModel):
    """Result panel for a VEX import.

    Counts are computed over *findings*, not raw statements: a statement that
    matches three component versions contributes three to ``matched`` and up to
    three to ``applied``. ``skipped`` + ``applied`` over the matched set, plus
    any unmatched statements, are reflected as ``errors`` rows.
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["openvex", "cyclonedx"] = Field(
        description="Auto-detected source format of the uploaded document.",
    )
    matched: int = Field(
        ge=0,
        description="Findings that a statement resolved to (before transition).",
    )
    applied: int = Field(
        ge=0,
        description="Findings whose status was actually changed.",
    )
    skipped: int = Field(
        ge=0,
        description=(
            "Findings/statements deliberately not applied (no-op, illegal "
            "transition, unknown vuln/purl, ambiguous match, …)."
        ),
    )
    errors: list[VEXImportItemError] = Field(
        default_factory=list,
        description="Structured per-statement skip/error reasons.",
    )


__all__ = [
    "VEXImportItemError",
    "VEXImportSkipReason",
    "VEXImportSummary",
]
