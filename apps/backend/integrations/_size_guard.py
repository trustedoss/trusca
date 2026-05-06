"""
JSONB row size guard — I-1.

The scan domain stores three JSONB columns under GIN indexes (PR #7's 0003
migration): ``scan_components.raw_data``, ``vulnerability_findings.analysis_response``,
and ``license_findings.raw_data``. Postgres' ``btree``/``gin`` page format
allows individual JSONB values up to ~1 GiB but practical operational limits
(replication latency, pg_dump size, GIN index bloat) make multi-megabyte rows
toxic. cdxgen / ORT / Trivy can occasionally emit unusually large per-component
metadata blobs (huge dependency trees, embedded license texts, raw SBOM
sub-trees) — we want to keep the scan succeeding, but with a bounded payload.

Policy:

- The default ceiling is 256 KiB per JSONB value (env-tunable via
  ``JSONB_ROW_SIZE_LIMIT_BYTES``).
- When a row exceeds the ceiling we replace it with a small marker dict
  documenting the truncation and preserving a 1 KiB textual preview so an
  operator can still see what was lost. The other rows in the same scan are
  unaffected — truncation is per-row, not per-scan.
- Truncation is non-fatal. We log a structured WARNING (not an audit log row;
  this is operational telemetry, not a user-visible event) so the scan still
  reaches ``status='succeeded'``.

This guard runs in the persistence layer of every scan task. It is also
exposed for unit testing via the public ``enforce_jsonb_row_size_limit``
helper.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from core.config import jsonb_row_size_limit_bytes

log = structlog.get_logger("integrations.size_guard")

_PREVIEW_LIMIT = 1024


def _serialize_size(payload: dict[str, Any]) -> int:
    """Return the JSON byte length of `payload` (UTF-8)."""
    # ``ensure_ascii=False`` matches the wire format Postgres stores so the
    # number we measure here matches the on-disk footprint.
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def _build_preview(payload: dict[str, Any]) -> str:
    """Return a short textual preview of `payload` capped at _PREVIEW_LIMIT."""
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) <= _PREVIEW_LIMIT:
        return raw
    return raw[:_PREVIEW_LIMIT] + "...<truncated>"


def enforce_jsonb_row_size_limit(
    payload: dict[str, Any],
    *,
    limit_bytes: int | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return `payload` if it fits the limit, else a truncation marker.

    Args:
        payload: The JSONB payload about to be persisted.
        limit_bytes: Override the ceiling (default = ``jsonb_row_size_limit_bytes()``).
        context: Diagnostic fields appended to the warning log line — typically
            ``{"scan_id": ..., "column": "scan_components.raw_data"}``.

    Returns:
        Either the original payload (when it fits) or a small dict::

            {
                "_truncated": True,
                "_original_size": 2_097_152,
                "_limit": 262_144,
                "_preview": "<first 1 KiB of the original JSON>",
                "summary": "<copied from payload['summary'] when present>"
            }

        Callers persist the returned dict directly. The truncated marker is
        intentionally JSONB-safe and well under the limit.
    """
    ceiling = limit_bytes if limit_bytes is not None else jsonb_row_size_limit_bytes()
    if not isinstance(payload, dict):  # defensive — DB column is jsonb-as-dict
        return payload

    size = _serialize_size(payload)
    if size <= ceiling:
        return payload

    marker: dict[str, Any] = {
        "_truncated": True,
        "_original_size": size,
        "_limit": ceiling,
        "_preview": _build_preview(payload),
    }
    summary = payload.get("summary")
    if isinstance(summary, str):
        # ``summary`` is the cdxgen / ORT canonical short field. Keeping it on
        # the marker means UI surfaces can still render a one-line description
        # of the component without hydrating the full blob.
        marker["summary"] = summary[:_PREVIEW_LIMIT]

    log.warning(
        "jsonb_row_truncated",
        original_size=size,
        limit=ceiling,
        **(context or {}),
    )
    return marker


__all__ = ["enforce_jsonb_row_size_limit"]
