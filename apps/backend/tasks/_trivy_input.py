# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The document Trivy reads, prepared once for every path that calls it.

Three stages hand Trivy an SBOM file — the source scan (the cdxgen output),
the SBOM ingest (the upload), and the rematch beat (the preserved SBOM). All
three need the same preparation, and needing it in only some of them would be
worse than needing it in none: a project whose findings appeared on the first
scan and vanished on the weekly rematch has been told something false twice.

Preparation today means one thing — :mod:`services.os_context`, which adds the
``operating-system`` component that distro packages need in order to match at
all. The seam is here rather than inside ``integrations.trivy`` because the
adapter's job is to run a binary on a file, not to decide what that file
should say; and because the enriched copy has to land in a workspace the
calling stage already owns and cleans up.

The original file is never modified. When there is something to add, the
enriched bytes are written to a new path under the caller's workspace and that
path is returned; otherwise the original path comes back unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog

from integrations._line_streamer import LineCallback
from services import os_context
from services.os_context import EnrichResult

log = structlog.get_logger("tasks.trivy_input")

#: Enrichment parses the whole document into memory, so it is bounded. The
#: upload endpoint caps SBOMs well below this and the rematch extractor caps
#: at 128 MiB; a file past this ceiling is handed to Trivy untouched rather
#: than risking the worker's memory over an enrichment that is, at worst, a
#: missed opportunity.
_MAX_ENRICH_BYTES = 64 * 1024 * 1024

#: Subdirectory of the caller's workspace holding the enriched copy. Named so
#: that an operator looking at a scan workspace can see the copy is ours.
_ENRICHED_DIRNAME = "os-context"


def prepare_trivy_sbom(
    sbom_path: Path,
    *,
    workspace: Path,
    scan_uuid: uuid.UUID | None = None,
    line_callback: LineCallback | None = None,
) -> Path:
    """Return the path Trivy should scan — the original, or an enriched copy.

    ``workspace`` must be a directory the caller owns and removes; the copy is
    written under ``{workspace}/os-context/`` with the original file name, so
    Trivy's content-based format detection sees exactly what it would have.

    Never raises. Every failure mode — unreadable file, oversized document,
    undecidable distro, a write that fails — falls back to the original path,
    which is what would have been scanned anyway.
    """
    try:
        result = _enrich(sbom_path)
    except OSError as exc:
        log.warning(
            "trivy_input_read_failed",
            scan_id=str(scan_uuid) if scan_uuid else None,
            error=str(exc)[:300],
        )
        return sbom_path

    if result is None:
        return sbom_path

    try:
        target_dir = workspace / _ENRICHED_DIRNAME
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / sbom_path.name
        target.write_bytes(result.document)
    except OSError as exc:
        log.warning(
            "trivy_input_write_failed",
            scan_id=str(scan_uuid) if scan_uuid else None,
            error=str(exc)[:300],
        )
        return sbom_path

    _announce(result, scan_uuid=scan_uuid, line_callback=line_callback)
    return target


def _enrich(sbom_path: Path) -> EnrichResult | None:
    """Read and enrich, skipping documents too large to hold in memory."""
    if sbom_path.stat().st_size > _MAX_ENRICH_BYTES:
        log.info("trivy_input_too_large_to_enrich", size=sbom_path.stat().st_size)
        return None
    return os_context.enrich_sbom_bytes(sbom_path.read_bytes())


def _announce(
    result: EnrichResult,
    *,
    scan_uuid: uuid.UUID | None,
    line_callback: LineCallback | None,
) -> None:
    """Say what was added, in the worker log and on the scan log.

    A user reading findings against packages their SBOM did not label with an
    operating system deserves to see where that label came from. The scan log
    is where they will look, so the line goes there too when the caller has
    one.
    """
    context = result.synthesized
    message = (
        f"[os-context] synthesized operating-system component "
        f"{context.name} {context.version} "
        f"({context.votes}/{context.total} distro packages) for CVE matching"
    )
    log.info(
        "trivy_input_os_synthesized",
        scan_id=str(scan_uuid) if scan_uuid else None,
        os_name=context.name,
        os_version=context.version,
        votes=context.votes,
        total=context.total,
    )

    if line_callback is not None:
        try:
            line_callback("stdout", message)
        except Exception:  # noqa: BLE001 — a log line must not fail a scan
            log.warning("trivy_input_announce_failed", exc_info=True)


__all__ = ["prepare_trivy_sbom"]
