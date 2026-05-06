"""
ScanCreate.metadata bounds — M-2 (security-reviewer finding).

Two caps on the JSONB `metadata` blob:

  - depth ≤ 4
  - serialized size ≤ 16 KiB (compact form, UTF-8 byte length)

Failures must surface as Pydantic ValidationError so the FastAPI handler
materializes RFC 7807 problem+json. The schema-level test is enough — the
HTTP shape is covered by the existing scans-api integration suite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Depth cap
# ---------------------------------------------------------------------------


def test_metadata_depth_4_is_accepted() -> None:
    from schemas.scan import ScanCreate

    # Depth 4: { ort: { rules: { ignore: [...] } } } is the canonical shape
    # the docstring calls out.
    payload = {"ort": {"rules": {"ignore": ["foo"]}}}
    scan = ScanCreate(metadata=payload)
    assert scan.metadata == payload


def test_metadata_depth_5_is_rejected() -> None:
    from schemas.scan import ScanCreate

    # 5 levels of nesting → must fail.
    payload = {"a": {"b": {"c": {"d": {"e": "leaf"}}}}}
    with pytest.raises(ValidationError) as info:
        ScanCreate(metadata=payload)
    msg = str(info.value).lower()
    # The error message references depth ("levels deep") and the cap (4).
    assert "level" in msg or "depth" in msg
    assert "4" in msg


def test_metadata_deeply_nested_via_lists_also_capped() -> None:
    """Lists count toward depth too, otherwise the cap is bypassable."""
    from schemas.scan import ScanCreate

    # Mix dicts and lists to push past the cap.
    payload: dict[str, Any] = {"a": [{"b": [{"c": [{"d": [{"e": "x"}]}]}]}]}
    with pytest.raises(ValidationError):
        ScanCreate(metadata=payload)


# ---------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------


def test_metadata_under_16kib_is_accepted() -> None:
    from schemas.scan import ScanCreate

    # 8 KiB blob — under the 16 KiB cap.
    payload = {"junk": "x" * 8 * 1024}
    scan = ScanCreate(metadata=payload)
    assert scan.metadata == payload


def test_metadata_over_16kib_is_rejected() -> None:
    from schemas.scan import ScanCreate

    # 32 KiB compact-encoded blob clearly exceeds the cap.
    payload = {"junk": "x" * 32 * 1024}
    encoded_size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    assert encoded_size > 16 * 1024  # sanity check the test input
    with pytest.raises(ValidationError) as info:
        ScanCreate(metadata=payload)
    assert "bytes" in str(info.value).lower() or "size" in str(info.value).lower()


def test_metadata_default_empty_passes() -> None:
    from schemas.scan import ScanCreate

    scan = ScanCreate()
    assert scan.metadata == {}


def test_metadata_validator_size_check_runs_on_compact_encoding() -> None:
    """The validator measures size on compact JSON (no whitespace).

    A dict whose pretty-printed form would exceed 16 KiB but whose compact
    form fits must still pass — confirms we use `separators=(",", ":")`.
    """
    from schemas.scan import ScanCreate

    # ~12 KiB compact, ~25 KiB if pretty-printed with indent=4.
    payload = {f"k_{i}": f"v_{i}" for i in range(800)}
    scan = ScanCreate(metadata=payload)
    assert scan.metadata == payload
