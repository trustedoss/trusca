"""
VEX (Vulnerability Exploitability eXchange) import / consume service — v2.1
Track A (A2).

Reads an uploaded VEX document (OpenVEX or CycloneDX VEX, format auto-detected)
and auto-transitions the project's matching findings to the status the document
asserts, suppressing triage noise. This is the inverse of A1's exporter
(``services/vex_export.py``): re-importing a document that was produced by the
exporter is a no-op at the status level (round-trip stability).

Why a dedicated import service
------------------------------
The exporter maps the internal 7-state status onto each VEX dialect's closed
vocabulary. A2 needs the *reverse* maps, plus three things the exporter never
had to do:

1. **Match** each statement back to concrete ``vulnerability_findings`` rows in
   the project's latest scan, keyed by ``(vulnerability external_id, purl)``.
2. **Transition legally** — the status state machine
   (``vulnerability_service.STATUS_TRANSITIONS``) forbids jumping straight from
   ``new`` to ``not_affected``; the legal path is ``new → analyzing →
   not_affected``. We compute a multi-step path and apply each hop so the audit
   trail records every legal step (and so a future matrix change can't be
   silently bypassed by the importer).
3. **Record provenance** — the transitioned row gets ``analysis_source =
   'vex_import'`` and ``vex_origin`` (the document @id/serialNumber, author,
   timestamp, and the VEX status the statement carried) so the audit trail and
   the UI can distinguish an imported status from a manual one.

Reverse status mapping (VEX → internal)
---------------------------------------
The exporter's mapping is *lossy* (several internal states collapse onto one
VEX status — ``not_affected``/``false_positive``/``suppressed`` all export to
OpenVEX ``not_affected``). The reverse map therefore picks a single canonical
internal target per VEX status. The canonical targets are chosen so the
round-trip is stable for the common cases:

OpenVEX (statement ``status``):

    OpenVEX status         internal target
    --------------------   ----------------
    not_affected           not_affected
    affected               exploitable
    fixed                  fixed
    under_investigation    analyzing

CycloneDX-VEX (``analysis.state``):

    CycloneDX state        internal target
    --------------------   ----------------
    not_affected           not_affected
    false_positive         false_positive
    exploitable            exploitable
    resolved               fixed
    in_triage              analyzing

``under_investigation`` / ``in_triage`` map to ``analyzing`` (not ``new``):
``new`` is the discovery inbox state and there is no legal transition *into*
``new``; a VEX document asserting "still investigating" is best represented as
``analyzing``.

Statement → finding matching
----------------------------
A statement carries a vulnerability id (CVE/GHSA/OSV name) and one or more
product/affects refs (purls). We resolve:

  - the ``Vulnerability`` row by ``external_id`` (exact, case-sensitive — VEX
    ids are canonical), and
  - the ``ComponentVersion`` rows by ``purl_with_version`` within the project's
    latest scan,

then intersect against ``vulnerability_findings`` for that scan. A statement
that resolves to zero findings (unknown vuln OR unknown purl in this scan) is
skipped with a structured reason; a statement whose vuln matches but whose purl
matches multiple component-versions is applied to each matching finding.

Transition legality + permissions
----------------------------------
Each hop is validated through the same matrix the manual PATCH path uses
(``_assert_can_transition``). Two outcomes are *expected, non-fatal* during a
bulk import and are recorded as skips rather than aborting the whole document:

  - **already_at_target** — the finding is already in the asserted status
    (idempotency / round-trip no-op).
  - **illegal_transition** — no legal path exists from the current status to the
    target (e.g. ``fixed → exploitable`` is not in the matrix). The finding is
    left untouched and reported.

A ``forbidden_transition`` (the actor lacks the role for a hop, e.g. a
non-team-admin trying to land in ``suppressed``) is likewise recorded per
statement; but note the endpoint is team-admin gated as a whole, so this is a
defense-in-depth path, not the common case.

Atomicity
---------
The whole import runs in one transaction: we apply every legal transition, then
commit once. A parse failure raises before any DB write. The audit listener
captures one audit row per status column change, so a multi-hop transition
(new → analyzing → not_affected) yields two audit rows — the legal step trail.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import bind_audit_team
from core.security import CurrentUser
from models import (
    ComponentVersion,
    Project,
    Vulnerability,
    VulnerabilityFinding,
)
from services.vulnerability_service import (
    STATUS_TRANSITIONS,
    VulnerabilityForbidden,
    VulnerabilityInvalidTransition,
    _assert_can_transition,
)

log = structlog.get_logger("vex_import.service")


# ---------------------------------------------------------------------------
# Limits (read at call time — CLAUDE.md core rule #11, no module-level env)
# ---------------------------------------------------------------------------


def _max_document_bytes() -> int:
    """Hard cap on the decoded VEX document size.

    A VEX document is small (one statement per finding); 8 MiB is generous and
    keeps a hostile multi-GB upload from exhausting memory during JSON parsing.
    Read at call time so an operator can override via ``VEX_IMPORT_MAX_BYTES``.
    """
    import os

    return int(os.getenv("VEX_IMPORT_MAX_BYTES", str(8 * 1024 * 1024)))


def _max_statements() -> int:
    """Cap on the number of statements/vulnerabilities processed per document.

    Bounds work + the size of the ``errors`` array against a pathological
    document with millions of tiny statements. Read at call time.
    """
    import os

    return int(os.getenv("VEX_IMPORT_MAX_STATEMENTS", "100000"))


# Cap on justification length we will persist from the document, mirroring the
# manual PATCH schema's ``max_length=4000``. Over-long impact statements are
# truncated, not rejected, so one verbose statement can't block the import.
_MAX_JUSTIFICATION_CHARS = 4000


# ---------------------------------------------------------------------------
# Domain exceptions (whole-document failures → RFC 7807 in the router)
# ---------------------------------------------------------------------------


class VEXImportError(Exception):
    """Base — each subclass carries an HTTP status used by the router."""

    status_code: int = 400
    title: str = "VEX Import Error"


class VEXImportTooLarge(VEXImportError):
    status_code = 413
    title = "VEX Document Too Large"


class VEXImportMalformed(VEXImportError):
    """422 — body is not valid JSON, or not a recognised VEX shape."""

    status_code = 422
    title = "Malformed VEX Document"


class VEXImportUnsupportedFormat(VEXImportError):
    """422 — JSON parsed but is neither OpenVEX nor CycloneDX VEX."""

    status_code = 422
    title = "Unsupported VEX Format"


# ---------------------------------------------------------------------------
# Reverse status maps (VEX → internal). Single source of truth for code + docs.
# ---------------------------------------------------------------------------

# OpenVEX statement ``status`` → internal target.
_OPENVEX_REVERSE_MAP: dict[str, str] = {
    "not_affected": "not_affected",
    "affected": "exploitable",
    "fixed": "fixed",
    "under_investigation": "analyzing",
}

# CycloneDX ``analysis.state`` → internal target.
_CYCLONEDX_REVERSE_MAP: dict[str, str] = {
    "not_affected": "not_affected",
    "false_positive": "false_positive",
    "exploitable": "exploitable",
    "resolved": "fixed",
    "in_triage": "analyzing",
}


# ---------------------------------------------------------------------------
# Parsed-statement value object
# ---------------------------------------------------------------------------


class _Statement:
    """A format-agnostic view of one VEX statement.

    ``vuln`` is the vulnerability id (CVE/GHSA/OSV name). ``products`` is the
    list of purls the statement applies to. ``target`` is the reverse-mapped
    internal status (or None if the VEX status was unmapped). ``justification``
    is the document's free-text rationale (impact_statement / analysis.detail).
    """

    __slots__ = ("vuln", "products", "vex_status", "target", "justification")

    def __init__(
        self,
        *,
        vuln: str | None,
        products: list[str],
        vex_status: str | None,
        target: str | None,
        justification: str | None,
    ) -> None:
        self.vuln = vuln
        self.products = products
        self.vex_status = vex_status
        self.target = target
        self.justification = justification


# ---------------------------------------------------------------------------
# Parsing + format detection (adversarial-input safe)
# ---------------------------------------------------------------------------


def _decode_json(raw: bytes) -> Any:
    """Decode UTF-8 + parse JSON, mapping every failure to VEXImportMalformed.

    Adversarial inputs handled here: non-UTF-8 bytes (incl. embedded null
    bytes that break the decoder), truncated/broken JSON, and a body that
    decodes to a non-object top level (a bare string / number / list at the
    OpenVEX root is not a valid document).
    """
    if len(raw) > _max_document_bytes():
        raise VEXImportTooLarge(
            f"VEX document exceeds the {_max_document_bytes()}-byte limit",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VEXImportMalformed("VEX document is not valid UTF-8") from exc
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        # RecursionError guards a pathologically deeply-nested document — the
        # stdlib decoder recurses per nesting level. We surface it as a clean
        # 422 instead of a 500.
        raise VEXImportMalformed("VEX document is not valid JSON") from exc
    return parsed


def _as_str(value: Any) -> str | None:
    """Return ``value`` iff it is a non-empty string after stripping, else None.

    Adversarial-input guard: every field we read from the untrusted document is
    funnelled through this so a number / list / dict / null / whitespace-only
    string where a string was expected becomes ``None`` (→ a structured skip),
    never a crash and never a non-string slipping into a DB string column.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _clean_justification(value: Any) -> str | None:
    """Sanitize a document free-text rationale for persistence.

    Strips control characters (CR/LF/null/other C0 except tab) that could be
    used for log injection or to smuggle a record separator, then truncates to
    the same 4000-char ceiling the manual PATCH schema enforces. Returns None
    for a non-string / empty value.

    Note: we do NOT HTML-escape here — the column stores raw analyst prose and
    the frontend is responsible for output encoding (React escapes by default).
    Stripping control chars is the storage-layer defense; XSS payloads that are
    plain printable text (``<script>...``) are stored verbatim and rendered
    inert by the UI's escaping, exactly as a manually-typed justification is.
    """
    if not isinstance(value, str):
        return None
    # Drop C0 control chars except TAB (\x09); this removes CR/LF (log/record
    # injection), NUL (Postgres TEXT cannot store \x00), and other control
    # bytes. We keep printable Unicode (incl. non-ASCII) intact.
    cleaned = "".join(
        ch for ch in value if ch == "\t" or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    ).strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_JUSTIFICATION_CHARS]


def _clean_provenance(value: Any) -> str | None:
    """Sanitize a document provenance field (@id / author / serialNumber /
    timestamp) for persistence in the ``vex_origin`` JSONB column.

    Mirrors :func:`_clean_justification`'s control-character / NUL stripping —
    the *only* difference is there is no length cap (provenance fields are short
    identifiers, not free prose). ``_as_str`` alone only ``strip()``s the ends,
    so an *embedded* NUL (``\\x00``) or C0 control byte survives and a JSONB
    commit then fails with a Postgres ``DataError`` — which would abort the
    whole import (a 500) and break the per-statement partial-failure contract.

    Drops C0 control chars except TAB (\\x09): removes CR/LF (log/record
    injection), NUL (Postgres TEXT/JSONB cannot store \\x00), and DEL (\\x7f);
    keeps printable Unicode (incl. non-ASCII) intact. Returns None for a
    non-string / empty value.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        ch for ch in value if ch == "\t" or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    ).strip()
    return cleaned or None


def _detect_and_parse(doc: Any) -> tuple[str, list[_Statement], dict[str, Any]]:
    """Detect the VEX dialect and parse it into ``(format, statements, origin)``.

    ``origin`` is the document-level provenance (@id/serialNumber, author,
    timestamp) recorded onto every transitioned finding's ``vex_origin``.

    Raises:
      VEXImportMalformed       — top level is not a JSON object.
      VEXImportUnsupportedFormat — object parses but matches no known dialect.
    """
    if not isinstance(doc, dict):
        raise VEXImportMalformed("VEX document root must be a JSON object")

    # CycloneDX VEX: ``bomFormat == 'CycloneDX'``.
    if doc.get("bomFormat") == "CycloneDX":
        statements, origin = _parse_cyclonedx(doc)
        return "cyclonedx", statements, origin

    # OpenVEX: ``@context`` references openvex.dev, OR a ``statements`` array is
    # present alongside an ``@id`` / ``author``. We accept either signal so a
    # document that omits @context (some producers do) still parses.
    ctx = _as_str(doc.get("@context")) or ""
    if "openvex" in ctx.lower() or isinstance(doc.get("statements"), list):
        statements, origin = _parse_openvex(doc)
        return "openvex", statements, origin

    raise VEXImportUnsupportedFormat(
        "document is neither OpenVEX (statements[]) nor CycloneDX VEX (bomFormat)",
    )


def _parse_openvex(doc: dict[str, Any]) -> tuple[list[_Statement], dict[str, Any]]:
    raw_statements = doc.get("statements")
    if not isinstance(raw_statements, list):
        raise VEXImportMalformed("OpenVEX 'statements' must be an array")
    if len(raw_statements) > _max_statements():
        raise VEXImportTooLarge(
            f"OpenVEX document has more than {_max_statements()} statements",
        )

    origin = {
        "format": "openvex",
        "id": _clean_provenance(doc.get("@id")),
        "author": _clean_provenance(doc.get("author")),
        "timestamp": _clean_provenance(doc.get("timestamp")),
    }

    statements: list[_Statement] = []
    for raw in raw_statements:
        if not isinstance(raw, dict):
            # Malformed individual statement → a skip row, not a hard failure.
            statements.append(
                _Statement(
                    vuln=None, products=[], vex_status=None, target=None, justification=None
                )
            )
            continue
        vuln_obj = raw.get("vulnerability")
        vuln = None
        if isinstance(vuln_obj, dict):
            vuln = _as_str(vuln_obj.get("name")) or _as_str(vuln_obj.get("@id"))
        else:
            vuln = _as_str(vuln_obj)

        products: list[str] = []
        raw_products = raw.get("products")
        if isinstance(raw_products, list):
            for p in raw_products:
                if isinstance(p, dict):
                    pid = _as_str(p.get("@id")) or _as_str(p.get("identifiers"))
                else:
                    pid = _as_str(p)
                if pid is not None:
                    products.append(pid)

        vex_status = _as_str(raw.get("status"))
        target = _OPENVEX_REVERSE_MAP.get(vex_status) if vex_status else None
        justification = _clean_justification(raw.get("impact_statement"))

        statements.append(
            _Statement(
                vuln=vuln,
                products=products,
                vex_status=vex_status,
                target=target,
                justification=justification,
            )
        )
    return statements, origin


def _parse_cyclonedx(doc: dict[str, Any]) -> tuple[list[_Statement], dict[str, Any]]:
    raw_vulns = doc.get("vulnerabilities")
    if not isinstance(raw_vulns, list):
        raise VEXImportMalformed("CycloneDX 'vulnerabilities' must be an array")
    if len(raw_vulns) > _max_statements():
        raise VEXImportTooLarge(
            f"CycloneDX document has more than {_max_statements()} vulnerabilities",
        )

    metadata = doc.get("metadata")
    timestamp = None
    author = None
    if isinstance(metadata, dict):
        timestamp = _clean_provenance(metadata.get("timestamp"))
        # CycloneDX has no single author; use the first tool name if present.
        tools = metadata.get("tools")
        if isinstance(tools, list) and tools and isinstance(tools[0], dict):
            author = _clean_provenance(tools[0].get("name"))

    origin = {
        "format": "cyclonedx",
        "id": _clean_provenance(doc.get("serialNumber")),
        "author": author,
        "timestamp": timestamp,
    }

    statements: list[_Statement] = []
    for raw in raw_vulns:
        if not isinstance(raw, dict):
            statements.append(
                _Statement(
                    vuln=None, products=[], vex_status=None, target=None, justification=None
                )
            )
            continue
        vuln = _as_str(raw.get("id"))

        products = []
        affects = raw.get("affects")
        if isinstance(affects, list):
            for a in affects:
                if isinstance(a, dict):
                    ref = _as_str(a.get("ref"))
                else:
                    ref = _as_str(a)
                if ref is not None:
                    products.append(ref)

        analysis = raw.get("analysis")
        vex_status = None
        justification = None
        if isinstance(analysis, dict):
            vex_status = _as_str(analysis.get("state"))
            justification = _clean_justification(analysis.get("detail"))
        target = _CYCLONEDX_REVERSE_MAP.get(vex_status) if vex_status else None

        statements.append(
            _Statement(
                vuln=vuln,
                products=products,
                vex_status=vex_status,
                target=target,
                justification=justification,
            )
        )
    return statements, origin


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


async def _load_findings_index(
    session: AsyncSession, *, scan_id: uuid.UUID
) -> dict[tuple[str, str], list[VulnerabilityFinding]]:
    """Build a ``(cve_external_id, purl) → [findings]`` index for the scan.

    One query loads every finding in the latest scan with its CVE id and purl
    so matching is O(1) per statement (no per-statement round-trip). The value
    is a *list* because the same (cve, purl) pair maps to one finding under the
    uq constraint, but we keep a list to surface an ``ambiguous_match`` if a
    data anomaly ever produces duplicates.
    """
    stmt = (
        select(
            VulnerabilityFinding,
            Vulnerability.external_id.label("cve_id"),
            ComponentVersion.purl_with_version.label("purl"),
        )
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == VulnerabilityFinding.component_version_id,
        )
        # No Component join: the purl comes from ComponentVersion and tenancy is
        # already enforced by ``scan_id`` (the scan belongs to the authorized
        # project), so joining Component added a row with no filtering value.
        .where(VulnerabilityFinding.scan_id == scan_id)
    )
    result = await session.execute(stmt)
    index: dict[tuple[str, str], list[VulnerabilityFinding]] = {}
    for finding, cve_id, purl in result.all():
        if cve_id is None or purl is None:
            continue
        index.setdefault((cve_id, purl), []).append(finding)
    return index


async def _known_cve_ids(session: AsyncSession, *, scan_id: uuid.UUID) -> set[str]:
    """All CVE ids that have at least one finding in the scan.

    Lets us distinguish ``unknown_vulnerability`` (CVE not in scan at all) from
    ``unknown_component`` (CVE present, but not on the statement's purl) for a
    precise skip reason.
    """
    stmt = (
        select(Vulnerability.external_id)
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(VulnerabilityFinding.scan_id == scan_id)
        .distinct()
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all() if row[0] is not None}


# ---------------------------------------------------------------------------
# Transition pathfinding
# ---------------------------------------------------------------------------


def _legal_path(current: str, target: str) -> list[str] | None:
    """Shortest legal transition path ``current → … → target`` (excl. current).

    BFS over ``STATUS_TRANSITIONS``. Returns the list of intermediate+final
    states to step through, ``[]`` if already at target (no-op), or ``None`` if
    no legal path exists.

    The matrix forbids direct ``new → not_affected``; BFS finds
    ``[analyzing, not_affected]`` so the importer applies the two legal hops and
    the audit trail records both. The path is bounded by the 7-state graph so
    BFS is trivial and always terminates.
    """
    if current == target:
        return []
    # BFS; predecessors map reconstructs the path.
    from collections import deque

    visited = {current}
    queue: deque[str] = deque([current])
    pred: dict[str, str] = {}
    while queue:
        node = queue.popleft()
        for nxt in STATUS_TRANSITIONS.get(node, frozenset()):
            if nxt in visited:
                continue
            visited.add(nxt)
            pred[nxt] = node
            if nxt == target:
                # Reconstruct path from target back to (but excluding) current.
                path = [target]
                cur = target
                while pred.get(cur) != current:
                    cur = pred[cur]
                    path.append(cur)
                path.reverse()
                return path
            queue.append(nxt)
    return None


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------


class _Result:
    """Mutable summary accumulated while applying statements."""

    def __init__(self) -> None:
        self.matched = 0
        self.applied = 0
        self.skipped = 0
        self.errors: list[dict[str, Any]] = []

    def skip(
        self,
        *,
        vuln: str | None,
        product: str | None,
        reason: str,
        detail: str,
    ) -> None:
        self.skipped += 1
        self.errors.append(
            {"vulnerability": vuln, "product": product, "reason": reason, "detail": detail}
        )


# ---------------------------------------------------------------------------
# Apply one finding transition
# ---------------------------------------------------------------------------


async def _apply_to_finding(
    session: AsyncSession,
    finding: VulnerabilityFinding,
    *,
    statement: _Statement,
    actor: CurrentUser,
    team_id: uuid.UUID,
    origin: dict[str, Any],
    result: _Result,
    now: datetime,
) -> None:
    """Step a single finding to ``statement.target`` via the legal path.

    Increments ``result.matched`` (the finding resolved) and either
    ``result.applied`` (status changed) or records a skip. Each legal hop is
    flushed separately so the ``before_flush`` audit listener records one audit
    row per status change (the legal-step trail); the caller commits once at the
    end.
    """
    result.matched += 1
    target = statement.target
    if target is None:
        # Fail-closed invariant guard. The caller already skips statements with
        # an unmapped status before reaching here, so this is unreachable in
        # normal flow — but an ``assert`` is stripped under ``python -O``, which
        # would let a ``None`` target slip into ``_legal_path`` and raise deeper.
        # Record a structured skip and return so the contract (one bad statement
        # never aborts the import) holds even in an optimized build.
        result.skip(
            vuln=statement.vuln,
            product=_first_product(statement),
            reason="unmapped_status",
            detail=f"VEX status {statement.vex_status!r} has no internal mapping",
        )
        return

    current = finding.status
    path = _legal_path(current, target)

    if path == []:
        # Idempotent no-op — already at target. Round-trip stability.
        result.skip(
            vuln=statement.vuln,
            product=_first_product(statement),
            reason="already_at_target",
            detail=f"finding already in status {target!r}",
        )
        return

    if path is None:
        result.skip(
            vuln=statement.vuln,
            product=_first_product(statement),
            reason="illegal_transition",
            detail=f"no legal path from {current!r} to {target!r}",
        )
        return

    # Validate every hop's role policy BEFORE mutating, so a forbidden hop
    # (e.g. landing in 'suppressed' without team_admin) leaves the row
    # untouched rather than half-transitioned.
    step_from = current
    for step_to in path:
        try:
            _assert_can_transition(
                actor,
                current_status=step_from,
                target_status=step_to,
                team_id=team_id,
            )
        except VulnerabilityForbidden:
            result.skip(
                vuln=statement.vuln,
                product=_first_product(statement),
                reason="forbidden_transition",
                detail=f"actor lacks role to transition into {step_to!r}",
            )
            return
        except VulnerabilityInvalidTransition:
            # _legal_path already guaranteed legality; this only fires if the
            # matrix and BFS disagree (programming error). Treat defensively.
            result.skip(
                vuln=statement.vuln,
                product=_first_product(statement),
                reason="illegal_transition",
                detail=f"matrix rejected {step_from!r} → {step_to!r}",
            )
            return
        step_from = step_to

    # Apply each hop in order, flushing per hop so the ``before_flush`` audit
    # listener records one audit row per status change (the legal-step trail).
    # ``_changed_columns`` reflects history since the last flush, so without the
    # per-hop flush a multi-step path would collapse to a single net diff
    # (new → not_affected) and lose the intermediate ``analyzing`` step.
    for step_to in path:
        finding.status = step_to
        finding.analysis_state = step_to
        finding.updated_at = now
        await session.flush()

    if statement.justification is not None:
        finding.analysis_justification = statement.justification
    finding.analyst_user_id = actor.id
    finding.analyzed_at = now
    finding.updated_at = now
    finding.analysis_source = "vex_import"
    finding.vex_origin = {
        **origin,
        # ``vex_status`` also lands in the JSONB column, so it gets the same
        # control-char / NUL stripping as the origin provenance fields — an
        # embedded NUL here would likewise fail the JSONB commit.
        "vex_status": _clean_provenance(statement.vex_status),
        "imported_at": now.isoformat(),
    }
    result.applied += 1


def _first_product(statement: _Statement) -> str | None:
    return statement.products[0] if statement.products else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def import_vex(
    session: AsyncSession,
    *,
    project: Project,
    raw: bytes,
    actor: CurrentUser,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Consume an uploaded VEX document, transitioning matching findings.

    ``project`` is the already-loaded, already-authorized project (the router
    runs IDOR + team-admin gating before calling us). ``raw`` is the raw
    uploaded bytes. Returns a summary dict shaped to
    :class:`schemas.vex_import.VEXImportSummary`.

    Raises (whole-document failures → RFC 7807 in the router):
      - VEXImportTooLarge          (413) — body / statement-count over the cap.
      - VEXImportMalformed         (422) — not UTF-8 / not JSON / wrong root.
      - VEXImportUnsupportedFormat (422) — JSON but no known VEX dialect.

    Per-statement problems (unknown vuln/purl, illegal transition, no-op) are
    NOT raised — they are recorded in the returned summary's ``errors`` list so
    one bad statement never aborts the whole import.
    """
    now = now or datetime.now(tz=UTC)

    # Capture the project's identifiers as plain locals up-front. After the
    # commit/rollback below the `project` ORM instance is expired (default
    # ``expire_on_commit``), so a later ``project.id`` access would trigger a
    # *synchronous* lazy reload — which blows up under asyncpg
    # (MissingGreenlet). Reading them now (the instance is live) avoids that.
    project_id = project.id
    team_id = project.team_id

    doc = _decode_json(raw)
    fmt, statements, origin = _detect_and_parse(doc)

    result = _Result()

    # Empty / no-scan project: nothing to match. Return a valid summary.
    if project.latest_scan_id is None:
        # Every statement is unmatchable; record an explicit reason per row so
        # the analyst sees why nothing applied.
        for st in statements[: _max_statements()]:
            result.skip(
                vuln=st.vuln,
                product=_first_product(st),
                reason="unknown_vulnerability",
                detail="project has no completed scan to match against",
            )
        return _summary(fmt, result)

    scan_id = project.latest_scan_id
    index = await _load_findings_index(session, scan_id=scan_id)
    known_cves = await _known_cve_ids(session, scan_id=scan_id)

    # Bind the project's team into the audit context so the audit rows the
    # listener writes for each status change carry team_id. user_id / request_id
    # are already bound by get_current_user + the request-id middleware.
    bind_audit_team(team_id)

    any_mutation = False
    for st in statements:
        # Malformed individual statement (non-dict in the array).
        if st.vuln is None:
            result.skip(
                vuln=None,
                product=_first_product(st),
                reason="malformed_statement",
                detail="statement has no resolvable vulnerability id",
            )
            continue
        if st.vex_status is None:
            result.skip(
                vuln=st.vuln,
                product=_first_product(st),
                reason="malformed_statement",
                detail="statement has no status/analysis.state",
            )
            continue
        if st.target is None:
            result.skip(
                vuln=st.vuln,
                product=_first_product(st),
                reason="unmapped_status",
                detail=f"VEX status {st.vex_status!r} has no internal mapping",
            )
            continue
        if not st.products:
            result.skip(
                vuln=st.vuln,
                product=None,
                reason="unknown_component",
                detail="statement carries no product/affects ref",
            )
            continue

        cve_known = st.vuln in known_cves
        matched_any_finding = False
        for purl in st.products:
            findings = index.get((st.vuln, purl), [])
            if not findings:
                # Distinguish "CVE not in scan" from "CVE present, wrong purl".
                if not cve_known:
                    result.skip(
                        vuln=st.vuln,
                        product=purl,
                        reason="unknown_vulnerability",
                        detail=f"{st.vuln} has no finding in the latest scan",
                    )
                else:
                    result.skip(
                        vuln=st.vuln,
                        product=purl,
                        reason="unknown_component",
                        detail=f"{st.vuln} has no finding on {purl} in the latest scan",
                    )
                continue
            if len(findings) > 1:
                result.skip(
                    vuln=st.vuln,
                    product=purl,
                    reason="ambiguous_match",
                    detail=f"{st.vuln} on {purl} matched {len(findings)} findings",
                )
                continue
            matched_any_finding = True
            before = result.applied
            await _apply_to_finding(
                session,
                findings[0],
                statement=st,
                actor=actor,
                team_id=team_id,
                origin=origin,
                result=result,
                now=now,
            )
            if result.applied > before:
                any_mutation = True

        if not matched_any_finding:
            # Already recorded per-purl above; no extra row needed.
            pass

    if any_mutation:
        await session.commit()
    else:
        # No DB writes; nothing to commit. Roll back any read-only state so the
        # session is clean for the next request.
        await session.rollback()

    log.info(
        "vex_imported",
        project_id=str(project_id),
        scan_id=str(scan_id),
        format=fmt,
        matched=result.matched,
        applied=result.applied,
        skipped=result.skipped,
        statements=len(statements),
        actor_id=str(actor.id),
        team_id=str(team_id),
    )

    return _summary(fmt, result)


def _summary(fmt: str, result: _Result) -> dict[str, Any]:
    return {
        "format": fmt,
        "matched": result.matched,
        "applied": result.applied,
        "skipped": result.skipped,
        "errors": result.errors,
    }


__all__ = [
    "VEXImportError",
    "VEXImportMalformed",
    "VEXImportTooLarge",
    "VEXImportUnsupportedFormat",
    "import_vex",
]
