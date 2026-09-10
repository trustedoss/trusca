# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``_detail_response`` must not silently drop a field the service computed.

History (issue #382)
---------------------
``_detail_response`` used to build ``VulnerabilityDetailResponse`` by naming
all 41 fields as keyword arguments by hand. A key the service payload already
carried but nobody added to that call was dropped silently: the model field
has a default, so it serialised as ``null`` and a client read "the server
does not have this value" while the database did. This happened twice (``kev``
/ ``kev_due_date`` during X1, then ER28a's ownership fields), and the original
guard here compared the call's keyword-argument set to the model's declared
fields via AST.

What changed and why the guard changed shape
---------------------------------------------
``_detail_response`` now builds the response with
``VulnerabilityDetailResponse.model_validate(payload)`` instead of a
hand-listed keyword call, and the model declares ``extra="forbid"``. That
removes the class of bug this file used to guard against structurally: there
is no per-field call site left to fall behind the payload, because there is
no per-field call site at all. A payload key the service adds is included
automatically; a payload key the schema does not declare now raises at
request time (500) instead of vanishing; a required field absent from the
payload raises too, because most fields here have no default.

Two things could still fail silently, so this file checks both directly
rather than trusting the refactor by inspection:

1. Someone reverts ``_detail_response`` to a hand-listed keyword call
   (possibly while "fixing" an unrelated type-checker complaint) and
   reintroduces the original defect. ``test_the_builder_still_uses_model_validate``
   is an AST regression guard for exactly that, in the same spirit as the
   old test: read the source structurally so it fails the moment the shape
   regresses, without needing a fixture.
2. ``extra="forbid"`` is declared on the model but never actually reached at
   runtime (for example if a `.model_dump()` round-trip somewhere strips
   unknown keys before validation, or if a different construction path
   bypasses it). ``test_extra_forbid_actually_rejects_an_unknown_payload_key``
   and ``test_missing_required_field_actually_raises`` build a payload by hand
   and drive it through ``model_validate`` to prove the guard fires, per the
   testing-guide rule that a new assertion must be shown breaking something
   before it is trusted.
"""

from __future__ import annotations

import ast
import copy
import pathlib
import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from schemas.vulnerability_detail import VulnerabilityDetailResponse

_API_MODULE = (
    pathlib.Path(__file__).resolve().parents[3] / "api" / "v1" / "vulnerabilities.py"
)


def _detail_response_function() -> ast.FunctionDef:
    tree = ast.parse(_API_MODULE.read_text())
    builder = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_detail_response"
        ),
        None,
    )
    assert builder is not None, "_detail_response is gone; this guard needs updating"
    return builder


def test_the_builder_still_uses_model_validate() -> None:
    """Regression guard for issue #382 taking its original shape back.

    Reads the source structurally (not a substring search) so a comment or a
    docstring mentioning the class name cannot satisfy it, and reformatting
    across lines cannot hide a regression from it either.
    """
    builder = _detail_response_function()

    uses_model_validate = False
    hand_lists_kwargs = False
    for node in ast.walk(builder):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "model_validate"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "VulnerabilityDetailResponse"
        ):
            uses_model_validate = True
        if isinstance(node.func, ast.Name) and node.func.id == "VulnerabilityDetailResponse":
            hand_lists_kwargs = True

    assert uses_model_validate, (
        "_detail_response no longer calls VulnerabilityDetailResponse.model_validate(...); "
        "if it now builds the response some other way, this guard needs updating to match, "
        "and the new construction path needs its own proof that it cannot drop a field."
    )
    assert not hand_lists_kwargs, (
        "_detail_response calls VulnerabilityDetailResponse(...) directly again. That is "
        "the exact shape issue #382 was filed against: a hand-listed keyword call silently "
        "drops any payload key nobody remembers to add. Use model_validate(payload) instead."
    )


def _full_detail_payload() -> dict[str, object]:
    """A complete, realistic detail payload: every field ``_build_detail_payload``
    (services/vulnerability_service.py) can produce, populated rather than left at
    a default, so the round trip through ``model_validate`` exercises every
    field and every nested structure once.
    """
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    return {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "scan_id": uuid.uuid4(),
        "cve_id": "CVE-2026-00001",
        "severity": "high",
        "cvss_score": 7.5,
        "epss_score": 0.42,
        "epss_percentile": 0.9,
        "kev": True,
        "kev_due_date": date(2026, 9, 20),
        "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "summary": "Example summary.",
        "details": "Example details.",
        "references": [{"url": "https://example.invalid/advisory"}],
        "matching_provenance": {
            "name": "GitHub Security Advisory npm",
            "id": "ghsa",
            "feed_url": "https://example.invalid/feed",
        },
        "published_at": now,
        "status": "analyzing",
        "analysis_state": "analyzing",
        "analysis_justification": None,
        "analysis_source": None,
        "vex_origin": None,
        "analyst_user_id": uuid.uuid4(),
        "analyzed_at": now,
        "reachable": True,
        "reachability_source": "govulncheck",
        "reachability_analyzed_at": now,
        "affected_components": [
            {
                "component_version_id": uuid.uuid4(),
                "name": "left-pad",
                "version": "1.0.0",
                "purl": "pkg:npm/left-pad@1.0.0",
                "fixed_version": "1.0.1",
            }
        ],
        "status_history": [
            {
                "actor_user_id": None,
                "action": "create",
                "previous_status": None,
                "new_status": "new",
                "created_at": now,
                "request_id": None,
            }
        ],
        "upgrade_recommendation": {
            "recommended_version": "1.0.1",
            "reason": "ok",
            "direct": True,
            "max_severity": "high",
            "max_epss": 0.42,
            "finding_count": 1,
        },
        "first_detected_at": now,
        "sla_due_date": now,
        "sla_status": "ok",
        "due_on": date(2026, 9, 15),
        "effective_due_date": now,
        "due_source": "sla",
        "manual_due_ignored": False,
        "assignee_user_id": uuid.uuid4(),
        "assignee_is_active": True,
        "ticket_url": "https://example.invalid/ticket/1",
        "ticket_key": "SEC-1",
        "ticket_status": "In Progress",
        "ticket_resolved": False,
        "ticket_checked_at": now,
        "ticket_check_error": None,
        "created_at": now,
        "updated_at": now,
    }


def test_full_payload_round_trips_through_model_validate() -> None:
    """A complete payload validates, and nested dict/list values become the
    typed nested models (AffectedComponent, VulnerabilityStatusHistoryEntry,
    UpgradeRecommendation, MatchingProvenance) without the per-item
    ``model_validate`` calls the old builder used to make by hand."""
    payload = _full_detail_payload()
    body = VulnerabilityDetailResponse.model_validate(payload)

    assert body.id == payload["id"]
    assert body.affected_components[0].name == "left-pad"
    assert body.status_history[0].new_status == "new"
    assert body.upgrade_recommendation is not None
    assert body.upgrade_recommendation.recommended_version == "1.0.1"
    assert body.matching_provenance is not None
    assert body.matching_provenance.name == "GitHub Security Advisory npm"


def test_every_declared_field_is_present_in_the_test_fixture() -> None:
    """Keeps ``_full_detail_payload`` honest: if a field is added to the model
    without a matching key here, the two tests above would validate a payload
    that is quietly missing coverage for it. Fails loudly instead."""
    declared = set(VulnerabilityDetailResponse.model_fields)
    fixture_keys = set(_full_detail_payload())
    missing = declared - fixture_keys
    assert not missing, (
        f"VulnerabilityDetailResponse declares {sorted(missing)} but "
        f"_full_detail_payload() in this test file does not set them; add them "
        f"so the extra=\"forbid\" / round-trip tests below actually exercise the "
        f"new field."
    )


def test_extra_forbid_actually_rejects_an_unknown_payload_key() -> None:
    """Proves ``extra=\"forbid\"`` is reached at runtime, not just declared.

    A payload key the schema does not know about must fail loudly (the caller
    sees a 500 via the FastAPI validation-error handler) rather than being
    dropped the way a missing keyword argument used to be.
    """
    payload = copy.deepcopy(_full_detail_payload())
    payload["a_field_the_schema_has_never_heard_of"] = "surprise"

    with pytest.raises(ValidationError, match="a_field_the_schema_has_never_heard_of"):
        VulnerabilityDetailResponse.model_validate(payload)


def test_missing_required_field_actually_raises() -> None:
    """The other half of the runtime proof: a required field the service
    forgot to compute must raise too, not serialise as a default the caller
    would misread as "the database has no value"."""
    payload = copy.deepcopy(_full_detail_payload())
    del payload["cve_id"]

    with pytest.raises(ValidationError, match="cve_id"):
        VulnerabilityDetailResponse.model_validate(payload)
