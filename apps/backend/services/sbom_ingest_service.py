"""
External CycloneDX SBOM ingest — synchronous validation + scan-row creation.

This is the synchronous *front half* of the SBOM-ingest feature: it accepts an
uploaded CycloneDX JSON document, validates it adversarially, persists a
``kind="sbom"`` :class:`~models.scan.Scan` row, writes the validated SBOM to a
durable on-disk location, and enqueues the Celery task that does the heavy work
(``tasks.ingest_sbom.ingest_sbom_task``). The endpoint returns ``202 Accepted``
with the queued scan row — never the result (CLAUDE.md core rule #3).

This endpoint is NOT the Dependency-Track ``/api/v1/bom`` + ``X-Api-Key`` BOM
upload surface — it is a first-party, RBAC-scoped portal endpoint.

Security posture (the upload is untrusted input — this is the PR's core attack
surface; recorded for the security reviewer):

  - **Unbounded buffering is forbidden.** The upload is read through a bounded,
    chunked loop that aborts the instant the running total crosses
    ``sbom_ingest_max_bytes()`` (default 32 MiB) — the whole body is never
    materialised first. Over-cap surfaces as 413. The endpoint additionally
    fast-fails on a declared ``Content-Length`` over the cap before reading a
    single body byte (mirrors the source-archive endpoint).

  - **Content-Type / filename allow-list.** Only JSON-ish media types and a
    ``.json`` / ``.cdx.json`` filename are accepted (415 otherwise). The header
    is advisory; the JSON parse + CycloneDX structure check are authoritative.

  - **Structural whitelist, NO deep traversal.** We parse the JSON and check
    only the TOP-LEVEL keys: ``bomFormat == "CycloneDX"``, ``specVersion`` in a
    known set, and (when present) ``components`` is a list whose ``len`` is
    within ``sbom_ingest_max_components()`` (default 50,000). We deliberately do
    NOT recurse into the component elements here — a deeply-nested hostile
    document cannot drive our validation into a recursion / CPU blow-up. The
    authoritative deep parse happens later, inside the Celery worker
    (``persist_sbom_components``), off the request path.

CLAUDE.md compliance:
  - Core rule #11: every limit is read via ``os.getenv`` at call time (through
    the ``core.config`` accessors) — no module-level env caching.
  - §4: failures raise typed domain exceptions carrying an HTTP status; the
    router maps them to RFC 7807 ``application/problem+json``.
  - Core rule #2: no schema change — the validated SBOM rides on disk and its
    path is carried in ``scan_metadata['sbom_path']`` (JSONB), so no migration.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import bind_audit_team
from core.config import (
    sbom_ingest_max_bytes,
    sbom_ingest_max_components,
    workspace_root,
)
from core.security import CurrentUser
from models import Scan
from services.scan_service import (
    ScanEnqueueFailed,
    ScanInProgressConflict,
    normalize_ref,
    prepare_scan_target,
)
from tasks import enqueue_scan

log = structlog.get_logger("sbom_ingest.service")

# Streaming chunk size for the bounded inbound read.
_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Content types a browser / CLI realistically sets for a CycloneDX JSON upload.
# The header is advisory (the JSON + structure checks are authoritative) but an
# obviously-wrong declaration fails fast with a clear 415 message.
_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/vnd.cyclonedx+json",
        "application/octet-stream",
        "",  # some CLIs omit the part content-type
    }
)

# CycloneDX spec versions we accept. The Celery persister handles the structural
# differences; here we only gate the declared version so a wildly-mismatched
# document is rejected up front.
_ALLOWED_SPEC_VERSIONS = frozenset({"1.2", "1.3", "1.4", "1.5", "1.6"})

# Reject a document whose structural nesting exceeds this before it ever reaches
# ``json.loads`` — the stdlib decoder recurses per nesting level and overflows
# the interpreter recursion limit (RecursionError → unhandled 500) on a
# maliciously deep document. A real CycloneDX SBOM nests only a handful of
# levels (metadata.component, nested components[], dependencies[]); 64 is far
# above any legitimate document yet well under CPython's default recursion
# ceiling, so the cheap O(n) byte scan below rejects the abuse case as a clean
# 422 instead of crashing the worker thread's stack.
_MAX_NESTING_DEPTH = 64


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class SbomIngestError(Exception):
    """Base class for SBOM-ingest errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "SBOM Ingest Error"
    type_uri: str = "https://docs.trustedoss.io/errors/sbom-ingest"


class SbomIngestTooLarge(SbomIngestError):
    status_code = 413
    title = "SBOM Too Large"
    type_uri = "https://docs.trustedoss.io/errors/sbom-ingest-too-large"


class SbomIngestUnsupportedType(SbomIngestError):
    status_code = 415
    title = "Unsupported SBOM Type"
    type_uri = "https://docs.trustedoss.io/errors/sbom-ingest-unsupported-type"


class SbomIngestInvalid(SbomIngestError):
    """The upload is not a valid / supported CycloneDX JSON document.

    Maps to 422 — the request was well-formed (right size, right media type) but
    the *content* is not an ingestible CycloneDX SBOM (not JSON, not an object,
    wrong ``bomFormat``, unsupported ``specVersion``, malformed ``components``,
    or too many components).
    """

    status_code = 422
    title = "Invalid SBOM Document"
    type_uri = "https://docs.trustedoss.io/errors/sbom-ingest-invalid"


class SbomIngestStorageError(SbomIngestError):
    """The validated SBOM could not be persisted to disk (transient infra fault).

    Maps to 503 — a server-side I/O failure (full/read-only volume, etc.), NOT a
    client error. Distinct from the 422 ``SbomIngestInvalid`` so a CI client
    treats it as retryable rather than surfacing a misleading "invalid SBOM".
    The client-facing message is generic (no path / errno leak).
    """

    status_code = 503
    title = "SBOM Storage Unavailable"
    type_uri = "https://docs.trustedoss.io/errors/sbom-ingest-storage"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def sbom_ingest_dir_for_project(project_id: uuid.UUID) -> Path:
    """Directory holding a project's durably-stored ingested SBOMs.

    Lives under ``workspace_root()`` but OUTSIDE any per-scan workspace
    (``{workspace_root()}/{scan_id}``), which the Celery task rmtrees on
    completion. The ingest task points its ``sbom_cyclonedx`` ScanArtifact at
    this durable copy so the SBOM signature/bundle download surface keeps working.
    """
    return Path(workspace_root()) / "sbom-ingest" / str(project_id)


def sbom_ingest_path(project_id: uuid.UUID, scan_id: uuid.UUID) -> Path:
    """Resolve the on-disk path for one ingested SBOM (keyed by scan id)."""
    return sbom_ingest_dir_for_project(project_id) / f"{scan_id}.cdx.json"


# ---------------------------------------------------------------------------
# Bounded read
# ---------------------------------------------------------------------------


async def _read_bounded(upload: UploadFile, *, max_bytes: int) -> bytes:
    """Read the whole upload into memory, but never more than ``max_bytes``.

    Reads in 1 MiB chunks and raises :class:`SbomIngestTooLarge` the instant the
    running total would exceed ``max_bytes`` — the body is NEVER buffered in full
    before the size is known, so an oversized upload cannot exhaust memory. We
    intentionally read up to ``max_bytes`` into memory (not stream-to-disk like
    the zip path) because the validated document must be JSON-parsed in full
    anyway; the cap keeps that bounded.
    """
    buf = bytearray()
    while True:
        chunk = await upload.read(_CHUNK_SIZE)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            log.warning(
                "sbom_ingest.reject_too_large",
                limit_bytes=max_bytes,
            )
            raise SbomIngestTooLarge(
                f"SBOM upload exceeds the {max_bytes}-byte ingest limit"
            )
    return bytes(buf)


# ---------------------------------------------------------------------------
# Validation (pure — unit-testable without DB / Redis)
# ---------------------------------------------------------------------------


def _validate_content_type(*, content_type: str | None, filename: str | None) -> None:
    """Reject media types / filenames that are not CycloneDX JSON (415).

    The check is advisory-but-fast-failing: a part content-type in the allow-list
    OR a ``.json`` / ``.cdx.json`` filename is accepted. A request that is wrong
    on BOTH axes is rejected with 415 before we even parse the body.
    """
    normalized_ct = (content_type or "").lower().split(";", 1)[0].strip()
    name = (filename or "").strip().lower()
    name_ok = name.endswith(".json") or name.endswith(".cdx.json")
    if normalized_ct in _ALLOWED_CONTENT_TYPES:
        return
    if name_ok:
        return
    log.warning(
        "sbom_ingest.reject_content_type",
        content_type=normalized_ct or "<none>",
        filename=name or "<none>",
    )
    raise SbomIngestUnsupportedType(
        f"content-type {normalized_ct!r} (filename {name!r}) is not an accepted "
        "CycloneDX JSON media type"
    )


def _max_nesting_depth(raw: bytes) -> int:
    """Return the maximum ``{``/``[`` nesting depth of ``raw``, string-aware.

    A single O(n) byte scan that tracks structural nesting while skipping the
    contents of JSON strings (so a license text or description containing ``{``
    never inflates the count) and honouring backslash escapes inside strings.
    Used as a pre-check so a pathologically deep document is rejected before the
    recursive ``json.loads`` decoder is invoked. No recursion here.
    """
    depth = 0
    max_depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # closing quote
                in_string = False
            continue
        if byte == 0x22:  # opening quote
            in_string = True
        elif byte == 0x7B or byte == 0x5B:  # '{' or '['
            depth += 1
            if depth > max_depth:
                max_depth = depth
        elif byte == 0x7D or byte == 0x5D:  # '}' or ']'
            if depth > 0:
                depth -= 1
    return max_depth


def validate_cyclonedx_document(raw: bytes) -> dict[str, Any]:
    """Parse + structurally validate an uploaded CycloneDX JSON document.

    Returns the parsed top-level dict on success. Raises :class:`SbomIngestInvalid`
    (422) on any of: non-JSON, non-object top level, ``bomFormat != "CycloneDX"``,
    unsupported ``specVersion``, ``components`` present but not a list, or more
    than ``sbom_ingest_max_components()`` components.

    Adversarial-input contract: we inspect ONLY top-level keys and ``len()`` of
    ``components`` — we never recurse into the component elements, so a deeply
    nested document cannot drive THIS function's own logic into a recursion / CPU
    blow-up. The authoritative deep parse runs later in the Celery worker, off
    the request path.

    ``json.loads`` itself, however, recurses one stack frame per nesting level
    and raises ``RecursionError`` on a maliciously deep document — which is NOT a
    ``ValueError`` and would otherwise escape as an unhandled 500. We guard that
    two ways: a cheap O(n) byte-level depth pre-check (rejecting before the
    decoder runs), and a defensive ``RecursionError`` catch around ``json.loads``
    for any deep input that slips under the byte-depth heuristic. Both surface as
    a clean 422. (Total size is already bounded by the cap applied upstream.)
    """
    depth = _max_nesting_depth(raw)
    if depth > _MAX_NESTING_DEPTH:
        raise SbomIngestInvalid(
            f"SBOM nesting depth {depth} exceeds the maximum {_MAX_NESTING_DEPTH}"
        )

    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise SbomIngestInvalid(f"upload is not valid JSON: {exc}") from exc
    except RecursionError as exc:  # pragma: no cover — byte pre-check catches first
        raise SbomIngestInvalid("SBOM document is too deeply nested") from exc

    if not isinstance(parsed, dict):
        raise SbomIngestInvalid("SBOM document top level is not a JSON object")

    if parsed.get("bomFormat") != "CycloneDX":
        raise SbomIngestInvalid(
            "document is not a CycloneDX SBOM (bomFormat != 'CycloneDX')"
        )

    spec_version = parsed.get("specVersion")
    if not isinstance(spec_version, str) or spec_version not in _ALLOWED_SPEC_VERSIONS:
        raise SbomIngestInvalid(
            f"unsupported CycloneDX specVersion {spec_version!r}; "
            f"supported versions are {sorted(_ALLOWED_SPEC_VERSIONS)}"
        )

    components = parsed.get("components")
    if components is not None:
        if not isinstance(components, list):
            raise SbomIngestInvalid("'components' must be a JSON array when present")
        max_components = sbom_ingest_max_components()
        if len(components) > max_components:
            raise SbomIngestInvalid(
                f"SBOM declares {len(components)} components; the maximum is "
                f"{max_components}"
            )

    return parsed


# ---------------------------------------------------------------------------
# Ingest — validate + persist scan row + write file + enqueue
# ---------------------------------------------------------------------------


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover — best-effort cleanup
        pass


# Operator-facing free-text metadata fields (release label, original filename)
# are bounded + control-byte-stripped before they land in the scan_metadata
# JSONB. ``ref`` is already sanitized by ``normalize_ref``; this keeps the other
# two from drifting on hardening (parity with trigger_scan's mask_pii pass) and
# stops an oversized/embedded-newline value from polluting audit diffs and the
# scan list UI. Returns None for an empty/whitespace value.
_META_TEXT_MAX_LEN = 255


def _clean_meta_text(value: str | None) -> str | None:
    if not value:
        return None
    # Strip C0/C1 control characters (incl. NUL, CR, LF, tab) — they have no
    # place in a release label or a filename and would corrupt log/audit lines.
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    if not cleaned:
        return None
    return cleaned[:_META_TEXT_MAX_LEN]


async def ingest_sbom(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    upload: UploadFile,
    actor: CurrentUser,
    ref: str | None = None,
    release: str | None = None,
) -> Scan:
    """Validate the uploaded SBOM, persist a queued scan row, and enqueue it.

    Guard order (CLAUDE.md §2 rule 1 — authz / existence ALWAYS before state):

      1. ``prepare_scan_target`` — existence/team-access (404/403), project-scoped
         API-key boundary (403), archived (409), per-team concurrency cap (429).
         Reuses ``trigger_scan``'s exact guard sequence + exceptions.
      2. Request validation — Content-Type/filename (415), size cap (413), JSON +
         CycloneDX structure (422). Runs AFTER the authz/state guards so a
         non-member learns nothing about a project from a malformed body.
      3. INSERT the scan row, flush. The partial unique index
         ``ix_scans_project_active`` makes this the atomic concurrency check: a
         second in-flight scan for the project raises :class:`ScanInProgressConflict`
         (409) at flush — AFTER the 404/403 above.
      4. Write the validated SBOM to its durable on-disk path (keyed by scan id),
         stamp ``scan_metadata['sbom_path']``, set ``project.latest_scan_id``,
         commit.
      5. Enqueue the Celery task; store the returned task id and commit. An enqueue
         failure marks the row ``failed`` and raises :class:`ScanEnqueueFailed`
         (503) — identical to ``trigger_scan``.

    Atomicity / cleanup rationale (why scan_id → file → enqueue is safe):

      * The scan id only exists after ``flush()``, and the file path is keyed by
        it, so the file can only be written for a row that already won the
        per-project active-scan race. A loser (409) never writes a file.
      * If the post-flush commit fails (e.g. the commit-time re-check of the
        unique index), we delete the just-written file before re-raising the 409,
        so no orphan SBOM is left for a scan row that does not exist.
      * If enqueue fails, the row is flipped to ``failed`` (the durable SBOM is
        left in place — it is small, keyed by the failed scan id, and the orphan
        workspace/retention sweep reclaims it; deleting it here would race a
        retry that re-dispatches the same row). This matches ``trigger_scan``'s
        "mark failed, surface 503" behaviour.
    """
    # ---- 1. authz / existence / state guards (shared with trigger_scan) -------
    project = await prepare_scan_target(session, project_id=project_id, actor=actor)

    # Bind the owning team into the audit context BEFORE the scan-row INSERT so
    # the before_flush audit listener stamps audit_logs.team_id (mirrors
    # trigger_scan). Without this the ingest mutation is audited with a NULL /
    # stale team_id and drops out of team-scoped audit views — exactly the
    # attribution an incident responder needs for an internet-facing surface.
    bind_audit_team(project.team_id)

    # ---- 2. request validation (untrusted input) -----------------------------
    _validate_content_type(content_type=upload.content_type, filename=upload.filename)
    raw = await _read_bounded(upload, max_bytes=sbom_ingest_max_bytes())
    # Structural whitelist; never deep-traverses component elements.
    validate_cyclonedx_document(raw)

    original_filename = _clean_meta_text(upload.filename)
    normalized_release = _clean_meta_text(release)

    # Capture identifiers BEFORE any commit so the except branches never touch an
    # expired ORM attribute (which would trigger a sync lazy-load on the async
    # engine). Plain locals are safe across rollback.
    project_id_value = project.id
    project_team_id = project.team_id

    # ---- 3. INSERT the scan row + flush to win the active-scan race -----------
    # sbom_path is filled in step 4 (it needs scan.id); seed it absent so the row
    # is well-formed even if the flush fails.
    scan = Scan(
        project_id=project_id_value,
        kind="sbom",
        status="queued",
        progress_percent=0,
        current_step=None,
        celery_task_id=None,  # set after enqueue
        requested_by_user_id=actor.id,
        scan_metadata={
            "source_type": "sbom",
            "release": normalized_release,
            "original_filename": original_filename,
        },
        # scan-retention: stamp the normalized ref so the ref-keyed retire query
        # (run when this scan later succeeds) is index-driven. NULL for ad-hoc.
        ref=normalize_ref(ref),
    )
    session.add(scan)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Partial unique index ix_scans_project_active (project_id WHERE status IN
        # ('queued','running')) — a scan is already in flight for this project.
        await session.rollback()
        raise ScanInProgressConflict(
            f"a scan is already queued or running for project {project_id_value}",
        ) from exc

    scan_id = scan.id

    # ---- 4. write the durable SBOM, stamp the path, commit --------------------
    dest = sbom_ingest_path(project_id_value, scan_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(raw)
    except OSError as exc:
        # The row was flushed but the SBOM could not be persisted; roll back so we
        # do not leave a queued row whose task will immediately abort on a missing
        # file. Surface as 503 (transient infra error) via SbomIngestStorageError.
        _unlink_quietly(dest)
        await session.rollback()
        log.error(
            "sbom_ingest.write_failed",
            project_id=str(project_id_value),
            scan_id=str(scan_id),
            error=type(exc).__name__,
        )
        # 503, not 422: a disk/IO fault is a transient server error, so a CI
        # client should retry rather than treat its valid SBOM as rejected.
        raise SbomIngestStorageError("failed to persist the uploaded SBOM") from exc

    # Record the durable path so the Celery task can load it. We rewrite the whole
    # metadata dict (rather than mutating in place) so the ORM change-tracking sees
    # the JSONB column as dirty.
    scan.scan_metadata = {
        "source_type": "sbom",
        "release": normalized_release,
        "original_filename": original_filename,
        "sbom_path": str(dest),
    }

    # Keep the denormalized pointer in sync so list pages show the queued scan
    # immediately (mirrors trigger_scan).
    project.latest_scan_id = scan_id

    try:
        await session.commit()
    except IntegrityError as exc:
        # Commit-time re-check of the unique index can still fire under a race.
        # Delete the just-written SBOM so no orphan file outlives the rolled-back
        # row, then surface the same 409 as the flush path.
        await session.rollback()
        _unlink_quietly(dest)
        raise ScanInProgressConflict(
            f"a scan is already queued or running for project {project_id_value}",
        ) from exc

    await session.refresh(scan)

    # ---- 5. enqueue the Celery task -------------------------------------------
    try:
        celery_task_id = enqueue_scan(scan)
    except Exception as exc:
        # The row exists in 'queued' but no worker will pick it up. Flip it to
        # 'failed' with the deterministic prefix trigger_scan uses so callers can
        # distinguish enqueue failures from pipeline failures. The durable SBOM is
        # left in place (see docstring).
        log.error(
            "sbom_ingest.enqueue_failed",
            scan_id=str(scan_id),
            project_id=str(project_id_value),
            error=str(exc),
            exc_info=True,
        )
        scan.status = "failed"
        scan.error_message = f"enqueue_failed: {exc}"
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()
        raise ScanEnqueueFailed(
            f"failed to enqueue SBOM ingest for project {project_id_value}: {exc}",
        ) from exc

    scan.celery_task_id = celery_task_id
    await session.commit()
    await session.refresh(scan)

    log.info(
        "sbom_ingest_queued",
        scan_id=str(scan_id),
        project_id=str(project_id_value),
        team_id=str(project_team_id),
        celery_task_id=celery_task_id,
        sbom_bytes=len(raw),
    )
    return scan


__all__ = [
    "SbomIngestError",
    "SbomIngestInvalid",
    "SbomIngestStorageError",
    "SbomIngestTooLarge",
    "SbomIngestUnsupportedType",
    "ingest_sbom",
    "sbom_ingest_dir_for_project",
    "sbom_ingest_path",
    "validate_cyclonedx_document",
]
