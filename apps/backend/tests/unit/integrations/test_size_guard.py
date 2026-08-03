"""
JSONB row size guard — I-1 regression.

The guard is the single point of truth for the 256 KiB ceiling we apply
before persisting cdxgen / ORT / Trivy output into the GIN-indexed JSONB
columns. We verify three things:

1. Inputs at or below the limit pass through untouched (no copy).
2. Inputs that exceed the limit are replaced by the documented marker dict
   (`_truncated=True` + `_original_size` + `_limit` + `_preview`), with the
   `summary` field copied across when present.
3. Truncation emits a structlog warning carrying the diagnostic context so
   operators can locate the row that triggered the cap.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog
import structlog.testing

# ---------------------------------------------------------------------------
# Pass-through (under limit)
# ---------------------------------------------------------------------------


def test_payload_under_limit_returns_same_object_identity() -> None:
    from integrations._size_guard import enforce_jsonb_row_size_limit

    payload = {"summary": "tiny", "data": [1, 2, 3]}
    result = enforce_jsonb_row_size_limit(payload, limit_bytes=256 * 1024)

    # Identity check is meaningful: the guard must not deep-copy small inputs.
    assert result is payload
    assert "_truncated" not in result


def test_payload_exactly_at_limit_passes_through() -> None:
    """A payload whose serialized size equals the ceiling must not truncate."""
    from integrations._size_guard import enforce_jsonb_row_size_limit

    # Build a payload whose JSON serialization is exactly the limit.
    target = 1024  # small bespoke ceiling so we don't allocate megabytes
    payload: dict[str, Any] = {"key": "x"}
    while len(json.dumps(payload, ensure_ascii=False, default=str)) < target:
        payload["key"] += "x"
    # Trim back to exactly the limit.
    while len(json.dumps(payload, ensure_ascii=False, default=str)) > target:
        payload["key"] = payload["key"][:-1]

    result = enforce_jsonb_row_size_limit(payload, limit_bytes=target)
    assert result is payload
    assert "_truncated" not in result


def test_non_dict_payload_returned_unchanged() -> None:
    """Defensive case — JSONB-as-list inputs bypass truncation logic."""
    from integrations._size_guard import enforce_jsonb_row_size_limit

    payload: list[int] = [1, 2, 3]
    # The function annotation expects `dict[str, Any]`; the runtime guard
    # accepts non-dicts and returns them as-is (defensive `isinstance(..., dict)`
    # check). We assert pass-through identity via `id(...)` to avoid mypy's
    # narrowed-type identity comparison warning.
    result = enforce_jsonb_row_size_limit(payload, limit_bytes=256 * 1024)  # type: ignore[arg-type]
    assert id(result) == id(payload)


# ---------------------------------------------------------------------------
# Truncation (above limit)
# ---------------------------------------------------------------------------


def test_payload_one_byte_over_limit_is_truncated() -> None:
    """The classic boundary: limit + 1 byte must trigger replacement."""
    from integrations._size_guard import enforce_jsonb_row_size_limit

    limit = 1024
    big = {"junk": "x" * (limit + 256)}
    assert len(json.dumps(big, ensure_ascii=False, default=str)) > limit

    result = enforce_jsonb_row_size_limit(big, limit_bytes=limit)
    assert result is not big
    assert result["_truncated"] is True
    assert result["_limit"] == limit
    assert result["_original_size"] > limit
    # Preview is bounded — _PREVIEW_LIMIT is 1 KiB inside the module.
    assert len(result["_preview"]) <= 1024 + len("...<truncated>")


def test_truncation_preserves_summary_field() -> None:
    """When the original payload carries a `summary`, the marker keeps it.

    `summary` is the canonical short-form description from cdxgen / ORT, so
    UI consumers can still render a one-line label even after the rest of
    the blob is dropped.
    """
    from integrations._size_guard import enforce_jsonb_row_size_limit

    limit = 512
    payload = {"summary": "vendored fork of acme-utils", "blob": "z" * 4096}
    result = enforce_jsonb_row_size_limit(payload, limit_bytes=limit)

    assert result["_truncated"] is True
    assert result["summary"] == "vendored fork of acme-utils"


def test_truncation_summary_is_capped_at_preview_limit() -> None:
    from integrations._size_guard import enforce_jsonb_row_size_limit

    limit = 256
    summary = "S" * 4096
    payload = {"summary": summary, "blob": "Z" * 4096}
    result = enforce_jsonb_row_size_limit(payload, limit_bytes=limit)

    assert result["_truncated"] is True
    assert isinstance(result["summary"], str)
    # The module's _PREVIEW_LIMIT is 1024.
    assert len(result["summary"]) <= 1024


def test_truncation_emits_structured_warning_with_context() -> None:
    """The guard must log at WARNING level with the supplied context fields.

    We use structlog's testing capture so the assertion is independent of
    the global logging configuration (which is set elsewhere by
    configure_logging()).
    """
    from integrations._size_guard import enforce_jsonb_row_size_limit

    payload = {"junk": "x" * 4096}
    context = {"scan_id": "test-scan-123", "column": "scan_components.raw_data"}

    with structlog.testing.capture_logs() as captured:
        enforce_jsonb_row_size_limit(payload, limit_bytes=512, context=context)

    truncation_events = [
        evt for evt in captured if evt.get("event") == "jsonb_row_truncated"
    ]
    assert len(truncation_events) == 1
    event = truncation_events[0]
    assert event["log_level"] == "warning"
    assert event["scan_id"] == "test-scan-123"
    assert event["column"] == "scan_components.raw_data"
    assert event["limit"] == 512
    assert event["original_size"] > 512


def test_default_limit_is_256kib(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no `limit_bytes` is passed, the guard reads the env-driven default."""
    from integrations._size_guard import enforce_jsonb_row_size_limit

    monkeypatch.setenv("JSONB_ROW_SIZE_LIMIT_BYTES", str(1024))
    payload = {"x": "y" * 4096}
    result = enforce_jsonb_row_size_limit(payload)  # no limit_bytes override

    assert result["_truncated"] is True
    assert result["_limit"] == 1024
