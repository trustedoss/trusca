"""
SBOM signature download service — v2.3-s3 (backend).

After a source scan generates the CycloneDX SBOM, the scan pipeline signs it with
cosign and (best-effort) builds an in-toto / SLSA provenance attestation, then
persists each artifact as a ``ScanArtifact`` row (``apps/backend/tasks/scan_source.py``):

  - ``sbom_cyclonedx``       — the original SBOM (CycloneDX JSON) the signature is over
  - ``sbom_cyclonedx_sig``   — the detached cosign signature (``sha256`` = SBOM digest)
  - ``sbom_cyclonedx_cert``  — (keyless only) the Fulcio signing certificate
  - ``sbom_attestation``     — the in-toto / DSSE SLSA provenance attestation
  - ``sbom_attest_cert``     — (keyless only) the attestation Fulcio certificate

This service is the read side: it locates those artifacts for a project's *latest
succeeded scan*, reads their bytes off disk under a strict trust boundary, and
also exposes the cosign **public key** so an external consumer can verify the
signature with ``cosign verify-blob --key cosign.pub`` without ever touching the
portal's private key.

The router (``api/v1/sbom.py``) is the thin HTTP adapter that wires auth + IDOR
+ Content-Disposition; all the file-locating / reading / trust-boundary logic
lives here so a background job (e.g. a scheduled compliance export) could reuse
it without booting FastAPI.

Trust boundary (security)
-------------------------
Every byte we serve is read via a ``ScanArtifact.storage_path`` value written by
the scan worker — never a request-derived path. As defense-in-depth we additionally
confine each read to the configured workspace root (``WORKSPACE_HOST_PATH``):
a ``storage_path`` that resolves outside the workspace, or that is a symlink
escaping it, is rejected as if the artifact did not exist (404). This means even
a corrupted / tampered ``storage_path`` row cannot turn the download endpoint
into an arbitrary-file-read primitive.

The cosign **public** key is read from ``COSIGN_PUBLIC_KEY_PATH`` (or the
``.pub`` derived from the private key path); the **private** key and its password
are NEVER read, returned, or logged by this module.

No-signature policy
-------------------
A scan that was never signed (signing skipped: no cosign binary, keyless cert
missing, key unconfigured, ...) simply has no ``sbom_cyclonedx_sig`` row. Each
locator returns ``None`` in that case and the router maps it to a 404 with an
RFC 7807 envelope. The bundle endpoint requires at least the SBOM + signature to
be present; a project with no signed scan yields a 404, not an empty zip.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import cosign_public_key_path, sbom_download_max_bytes, workspace_root
from models import Project, Scan, ScanArtifact

log = structlog.get_logger("sbom_signature.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class SBOMSignatureError(Exception):
    """Base — each subclass carries an HTTP status used by the router."""

    status_code: int = 400
    title: str = "SBOM Signature Error"


class SBOMSignatureNotFound(SBOMSignatureError):
    """The requested signing artifact does not exist for this project's latest scan."""

    status_code = 404
    title = "Signature Artifact Not Found"


class SBOMArtifactTooLarge(SBOMSignatureError):
    """A signing artifact (or the bundle total) exceeds the configured size cap.

    Raised by the read path when ``ScanArtifact.byte_size`` (or the actual bytes
    read, or the running bundle total) crosses :func:`config.sbom_download_max_bytes`.
    The router maps it to a 413 (Payload Too Large) RFC 7807 envelope rather than
    streaming a multi-GiB body that could OOM the API process.
    """

    status_code = 413
    title = "SBOM Artifact Too Large"


# ---------------------------------------------------------------------------
# ScanArtifact.kind constants (mirror tasks/scan_source.py — single source of
# truth is the producer, repeated here so the read side does not import a Celery
# task module, which would drag the scan pipeline into the HTTP import graph).
# ---------------------------------------------------------------------------

KIND_SBOM = "sbom_cyclonedx"
KIND_SIGNATURE = "sbom_cyclonedx_sig"
KIND_CERTIFICATE = "sbom_cyclonedx_cert"
KIND_ATTESTATION = "sbom_attestation"
KIND_ATTEST_CERT = "sbom_attest_cert"


# ---------------------------------------------------------------------------
# Artifact descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignatureArtifact:
    """A signing artifact resolved to bytes, ready to stream as a download.

    ``content`` are the raw file bytes (read under the trust boundary).
    ``filename`` is an operator-friendly download name (``sbom-<slug>.<ext>``).
    ``media_type`` is the HTTP Content-Type. ``sha256`` carries the SBOM digest
    for the signature artifact (so a consumer can bind sig → exact bytes) and is
    ``None`` for the others.
    """

    content: bytes
    filename: str
    media_type: str
    sha256: str | None = None


# Per-kind download metadata: (file extension, media type). cosign signatures /
# certs / attestations are opaque text; we serve them as octet-stream so a
# browser offers "save as" rather than trying to render them.
_DOWNLOAD_META: dict[str, tuple[str, str]] = {
    KIND_SBOM: ("cdx.json", "application/json"),
    KIND_SIGNATURE: ("cdx.json.sig", "application/octet-stream"),
    KIND_CERTIFICATE: ("cdx.json.cert.pem", "application/x-pem-file"),
    KIND_ATTESTATION: ("intoto.jsonl", "application/octet-stream"),
    KIND_ATTEST_CERT: ("attest.cert.pem", "application/x-pem-file"),
}

_PUBLIC_KEY_MEDIA_TYPE = "application/x-pem-file"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project | None:
    result = await session.execute(select(Project).where(Project.id == project_id))
    return result.scalar_one_or_none()


async def _load_latest_succeeded_scan(
    session: AsyncSession, *, project_id: uuid.UUID
) -> Scan | None:
    """Return the most recent ``status='succeeded'`` scan for the project, or None.

    Mirrors ``services.sbom_export._load_latest_succeeded_scan`` so the signature
    surface and the SBOM surface always agree on *which* scan they describe — the
    signature must be over the same SBOM the export endpoint serves.
    """
    stmt = (
        select(Scan)
        .where(Scan.project_id == project_id)
        .where(Scan.status == "succeeded")
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _load_artifact_path(
    session: AsyncSession, *, scan_id: uuid.UUID, kind: str
) -> tuple[str, str | None, int | None] | None:
    """Return ``(storage_path, sha256, byte_size)`` for the newest artifact of ``kind``.

    A re-run clears prior ``scan_artifacts`` before re-signing, so at most one row
    of each kind exists per scan; we still ``ORDER BY created_at DESC`` + ``LIMIT
    1`` defensively so a hypothetical duplicate resolves deterministically to the
    most recent. Returns ``None`` when the scan has no artifact of that kind
    (e.g. signing was skipped, or key-based signing emits no certificate).

    ``byte_size`` is the producer-persisted file size (may be ``None`` on older
    rows); the read path uses it as a cheap pre-read DoS guard.
    """
    stmt = (
        select(ScanArtifact.storage_path, ScanArtifact.sha256, ScanArtifact.byte_size)
        .where(ScanArtifact.scan_id == scan_id)
        .where(ScanArtifact.kind == kind)
        .order_by(ScanArtifact.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return None
    return row[0], row[1], row[2]


# ---------------------------------------------------------------------------
# Trust-boundary file read
# ---------------------------------------------------------------------------


def _read_within_workspace(storage_path: str, *, max_bytes: int) -> bytes | None:
    """Read ``storage_path`` iff it resolves to a regular file inside the workspace.

    Security: ``storage_path`` comes from a ``ScanArtifact`` row written by the
    scan worker, but we treat it as defense-in-depth-untrusted: a corrupted or
    tampered row must never let this endpoint read an arbitrary file. We resolve
    both the configured workspace root and the candidate path (following
    symlinks) and require the candidate to live *under* the workspace root.

    DoS guard: before reading, we ``stat`` the resolved file and reject anything
    larger than ``max_bytes`` by raising :class:`SBOMArtifactTooLarge` (mapped to
    413). This is the authoritative on-disk check; the DB ``byte_size`` pre-check
    in the caller is a cheaper short-circuit, but ``byte_size`` is an untrusted
    row so we never rely on it alone.

    Returns the file bytes, or ``None`` when the path escapes the workspace, is
    not a regular file, or cannot be read. ``None`` surfaces to the caller as a
    404 (treated identically to "artifact row absent"). Raises
    :class:`SBOMArtifactTooLarge` when the file exceeds the cap.
    """
    try:
        root = Path(workspace_root()).resolve()
        candidate = Path(storage_path).resolve()
    except OSError:
        return None

    # Path-traversal / workspace-escape guard. ``is_relative_to`` (3.9+) is the
    # canonical containment check; combined with ``.resolve()`` above it also
    # defeats a symlink that points outside the workspace (the resolved target is
    # what we compare).
    if not candidate.is_relative_to(root):
        log.warning(
            "sbom_signature_path_escape",
            # Log the resolved candidate (not the raw row value) so an operator
            # can see WHERE it pointed; never log file contents.
            resolved=str(candidate),
            workspace=str(root),
        )
        return None

    try:
        if not candidate.is_file():
            return None
        # On-disk size guard BEFORE reading bytes — never pull a multi-GiB file
        # into memory. stat() follows the (already-resolved) path.
        actual_size = candidate.stat().st_size
        if actual_size > max_bytes:
            log.error(
                "sbom_signature_artifact_too_large",
                resolved=str(candidate),
                actual_size=actual_size,
                max_bytes=max_bytes,
            )
            raise SBOMArtifactTooLarge(
                f"artifact is {actual_size} bytes, exceeding the {max_bytes}-byte cap"
            )
        return candidate.read_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Public locators (used by the router)
# ---------------------------------------------------------------------------


async def get_signature_artifact(
    session: AsyncSession, *, project: Project, kind: str
) -> SignatureArtifact | None:
    """Resolve a single signing artifact (by kind) for the project's latest scan.

    Returns ``None`` when the project has no succeeded scan, no artifact row of
    the requested kind, or the file is missing / outside the workspace. The
    router maps ``None`` to a 404 RFC 7807 envelope.
    """
    if kind not in _DOWNLOAD_META:  # pragma: no cover - guarded by the router enum
        raise SBOMSignatureNotFound(f"unknown signing artifact kind {kind!r}")

    scan = await _load_latest_succeeded_scan(session, project_id=project.id)
    if scan is None:
        return None

    located = await _load_artifact_path(session, scan_id=scan.id, kind=kind)
    if located is None:
        return None
    storage_path, sha256, byte_size = located

    max_bytes = sbom_download_max_bytes()
    # Cheap pre-read short-circuit on the persisted size (untrusted row, so the
    # on-disk stat in _read_within_workspace is still the authoritative check).
    if byte_size is not None and byte_size > max_bytes:
        log.error(
            "sbom_signature_artifact_too_large",
            kind=kind,
            byte_size=byte_size,
            max_bytes=max_bytes,
            source="db",
        )
        raise SBOMArtifactTooLarge(
            f"artifact is {byte_size} bytes, exceeding the {max_bytes}-byte cap"
        )

    content = _read_within_workspace(storage_path, max_bytes=max_bytes)
    if content is None:
        return None

    ext, media_type = _DOWNLOAD_META[kind]
    return SignatureArtifact(
        content=content,
        filename=f"sbom-{project.slug}.{ext}",
        media_type=media_type,
        sha256=sha256,
    )


# PEM header markers used to validate that what we are about to serve as a
# "public key" is in fact public material — and, critically, to REFUSE to serve
# a private key. Compared case-insensitively against the file's leading bytes.
_PRIVATE_KEY_MARKERS: tuple[bytes, ...] = (
    b"PRIVATE KEY",  # "BEGIN PRIVATE KEY", "BEGIN RSA/EC/OPENSSH PRIVATE KEY"
    b"BEGIN ENCRYPTED",  # "BEGIN ENCRYPTED PRIVATE KEY"
)
_PUBLIC_KEY_MARKERS: tuple[bytes, ...] = (
    b"PUBLIC KEY",  # "BEGIN PUBLIC KEY"
    b"BEGIN CERTIFICATE",  # an x509 cert is also acceptable public material
)
# Only inspect the leading bytes for the PEM header; a key/cert header lives in
# the first line. Bounded so a pathological file cannot drive the scan.
_PEM_HEADER_SNIFF_BYTES = 1024


def _read_public_key() -> bytes | None:
    """Read the cosign PUBLIC key bytes, or ``None`` when unavailable / unsafe.

    Resolves the path via :func:`core.config.cosign_public_key_path` (an explicit
    ``COSIGN_PUBLIC_KEY_PATH`` or the ``.pub`` derived from the private key path).
    The PRIVATE key is never *intended* to be read here — only the ``.pub``.

    Security (v2.3-s3 hardening): the resolved path is operator-configured, so a
    misconfiguration (``COSIGN_PUBLIC_KEY_PATH`` pointed at the private key, or a
    ``.pub`` symlink that targets the private key) would otherwise silently leak
    the private key to every developer with download access — a catastrophic
    confidentiality failure. To make that failure *loud and safe* we sniff the
    PEM header after reading and:
      - REFUSE (ERROR log + ``None`` -> 404) if it carries a PRIVATE-KEY marker;
      - REFUSE if it is not recognisably PUBLIC material (a PUBLIC KEY or a
        CERTIFICATE header).
    This converts a silent catastrophic disclosure into a logged 404.

    Returns ``None`` when no public key path is configured (keyless mode), the
    file is missing, or the content guard rejects it, so the router can advise
    certificate-based verification instead.

    No workspace confinement here: the public key is an operator-configured path
    (typically a mounted secret volume), exactly like ``cosign_key_path``. We do
    require it to be an existing regular file.
    """
    path_raw = cosign_public_key_path()
    if path_raw is None:
        return None
    try:
        path = Path(path_raw)
        if not path.is_file():
            return None
        content = path.read_bytes()
    except OSError:
        return None

    head = content[:_PEM_HEADER_SNIFF_BYTES].upper()

    if any(marker in head for marker in _PRIVATE_KEY_MARKERS):
        # Misconfiguration: the configured public-key path resolves to a PRIVATE
        # key. Never serve it. Log loudly (no contents) so an operator notices.
        log.error(
            "sbom_public_key_is_private_key_refused",
            resolved=str(path.resolve()) if _safe_resolve(path) else str(path),
        )
        return None

    if not any(marker in head for marker in _PUBLIC_KEY_MARKERS):
        # Not recognisably public material (PUBLIC KEY / CERTIFICATE). Refuse
        # rather than serve an unknown blob the operator misconfigured.
        log.error(
            "sbom_public_key_not_public_material_refused",
            resolved=str(path.resolve()) if _safe_resolve(path) else str(path),
        )
        return None

    return content


def _safe_resolve(path: Path) -> bool:
    """True iff ``path.resolve()`` does not raise (used only for safe logging)."""
    try:
        path.resolve()
    except OSError:
        return False
    return True


def get_public_key(*, project: Project) -> SignatureArtifact | None:
    """Resolve the cosign public key as a downloadable artifact, or ``None``.

    The public key is deployment-global (one cosign key signs every project's
    SBOMs), so it does not depend on a scan — but we still take ``project`` so
    the download filename is project-scoped and the router can keep a uniform
    auth/IDOR shape across the signature surface. ``None`` (no key configured /
    keyless) maps to a 404 with a "use certificate verification" hint.
    """
    content = _read_public_key()
    if content is None:
        return None
    return SignatureArtifact(
        content=content,
        filename="cosign.pub",
        media_type=_PUBLIC_KEY_MEDIA_TYPE,
    )


# ---------------------------------------------------------------------------
# Verification README (shipped inside the bundle)
# ---------------------------------------------------------------------------


def _sanitize_inline(value: str) -> str:
    """Strip control characters / newlines from free text before interpolation.

    ``project.name`` is operator-provided free text interpolated into the plain
    VERIFY.md inside the zip. There is no XSS surface (it is a text file, not
    HTML), but a name with embedded newlines / control chars could forge extra
    "lines" in the README or corrupt a terminal that cats it — belt-and-braces.
    The filename logic uses ``project.slug`` (already a safe slug) so this only
    touches the human-readable heading.
    """
    return "".join(ch for ch in value if ch == " " or (ch.isprintable() and ch not in "\r\n"))


def _verification_readme(*, project: Project, has_public_key: bool, has_attestation: bool) -> str:
    """Plain-text instructions for verifying the bundle with cosign.

    Tailored to what the bundle actually contains: key-based vs certificate-based
    verification, and the attestation command when an attestation is present.
    No secrets — the bundle ships only public material.
    """
    lines = [
        f"# Verifying the SBOM signature for project: {_sanitize_inline(project.name)}",
        "",
        "This bundle lets you independently verify, with cosign, that the SBOM",
        "in this archive was produced and signed by this TRUSCA deployment.",
        "",
        "Files:",
        "  sbom-*.cdx.json        the CycloneDX SBOM (the signed bytes)",
        "  sbom-*.cdx.json.sig    the detached cosign signature over the SBOM",
    ]
    if has_public_key:
        lines.append("  cosign.pub             the cosign PUBLIC key (key-based verification)")
    else:
        lines.append("  sbom-*.cdx.json.cert.pem  the Fulcio certificate (keyless verification)")
    if has_attestation:
        lines.append("  sbom-*.intoto.jsonl    the in-toto / SLSA provenance attestation")
        if not has_public_key:
            # Keyless attestation also ships its Fulcio certificate so a consumer
            # can run `cosign verify-attestation` from the bundle alone.
            lines.append(
                "  sbom-*.attest.cert.pem the Fulcio cert for the attestation (keyless)"
            )
    lines += [
        "",
        "## Verify the signature",
        "",
    ]
    if has_public_key:
        lines += [
            "Key-based (this deployment uses a cosign key pair):",
            "",
            "    cosign verify-blob \\",
            "      --key cosign.pub \\",
            "      --signature sbom-*.cdx.json.sig \\",
            "      sbom-*.cdx.json",
            "",
        ]
    else:
        lines += [
            "Keyless (this deployment uses sigstore Fulcio/Rekor):",
            "",
            "    cosign verify-blob \\",
            "      --certificate sbom-*.cdx.json.cert.pem \\",
            "      --certificate-identity <expected-identity> \\",
            "      --certificate-oidc-issuer <expected-issuer> \\",
            "      --signature sbom-*.cdx.json.sig \\",
            "      sbom-*.cdx.json",
            "",
        ]
    if has_attestation:
        lines += [
            "## Inspect the provenance attestation",
            "",
            "The attestation is an in-toto Statement (SLSA provenance) in a DSSE",
            "envelope. Decode its payload to inspect how the SBOM was produced:",
            "",
            "    jq -r '.payload' sbom-*.intoto.jsonl | base64 -d | jq .",
            "",
        ]
        if has_public_key:
            lines += [
                "Verify the attestation with the cosign public key:",
                "",
                "    cosign verify-blob-attestation \\",
                "      --key cosign.pub \\",
                "      --bundle sbom-*.intoto.jsonl \\",
                "      sbom-*.cdx.json",
                "",
            ]
        else:
            lines += [
                "Verify the keyless attestation with its Fulcio certificate:",
                "",
                "    cosign verify-blob-attestation \\",
                "      --certificate sbom-*.attest.cert.pem \\",
                "      --certificate-identity <expected-identity> \\",
                "      --certificate-oidc-issuer <expected-issuer> \\",
                "      --bundle sbom-*.intoto.jsonl \\",
                "      sbom-*.cdx.json",
                "",
            ]
    lines += [
        "A successful 'Verified OK' from cosign means the SBOM bytes are intact",
        "and were signed by this deployment's signing identity.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bundle (zip)
# ---------------------------------------------------------------------------


async def build_signature_bundle(
    session: AsyncSession, *, project: Project
) -> SignatureArtifact | None:
    """Assemble a zip of SBOM + signature + (cert | public key) + attestation + README.

    Returns ``None`` when the project's latest succeeded scan has no SBOM or no
    signature — the bundle's whole purpose is external signature verification, so
    a bundle without the signed bytes + signature would be useless. With those
    two present we add whatever else exists (cert, public key, attestation) plus a
    verification README tailored to key-based vs keyless.

    The zip is built fully in memory (SBOM bodies are small — typically << 1 MB)
    so we never write a temp file or stream from disk in the request path.
    """
    scan = await _load_latest_succeeded_scan(session, project_id=project.id)
    if scan is None:
        return None

    # The two mandatory members. Without both, a bundle cannot be verified.
    sbom = await get_signature_artifact(session, project=project, kind=KIND_SBOM)
    signature = await get_signature_artifact(session, project=project, kind=KIND_SIGNATURE)
    if sbom is None or signature is None:
        return None

    certificate = await get_signature_artifact(session, project=project, kind=KIND_CERTIFICATE)
    attestation = await get_signature_artifact(session, project=project, kind=KIND_ATTESTATION)
    # The keyless attestation Fulcio certificate is only useful (and only exists)
    # when there is an attestation to verify; without it a keyless consumer cannot
    # run ``cosign verify-attestation`` from the bundle alone. Fetch it only when
    # an attestation is present so the bundle stays self-consistent.
    attest_cert = (
        await get_signature_artifact(session, project=project, kind=KIND_ATTEST_CERT)
        if attestation is not None
        else None
    )
    public_key = get_public_key(project=project)

    readme = _verification_readme(
        project=project,
        has_public_key=public_key is not None,
        has_attestation=attestation is not None,
    )

    # Cap the bundle's running total so a set of large (but individually
    # under-cap) members cannot buffer an unbounded zip into memory.
    max_bytes = sbom_download_max_bytes()
    members = [sbom, signature, certificate, public_key, attestation, attest_cert]
    total = sum(len(m.content) for m in members if m is not None)
    if total > max_bytes:
        log.error(
            "sbom_signature_bundle_too_large",
            project_id=str(project.id),
            scan_id=str(scan.id),
            total_bytes=total,
            max_bytes=max_bytes,
        )
        raise SBOMArtifactTooLarge(
            f"signature bundle is {total} bytes, exceeding the {max_bytes}-byte cap"
        )

    buffer = io.BytesIO()
    # ZIP_DEFLATED keeps the archive small; the members are mostly text.
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(sbom.filename, sbom.content)
        zf.writestr(signature.filename, signature.content)
        if certificate is not None:
            zf.writestr(certificate.filename, certificate.content)
        if public_key is not None:
            zf.writestr(public_key.filename, public_key.content)
        if attestation is not None:
            zf.writestr(attestation.filename, attestation.content)
        if attest_cert is not None:
            zf.writestr(attest_cert.filename, attest_cert.content)
        zf.writestr("VERIFY.md", readme)

    log.info(
        "sbom_signature_bundle_built",
        project_id=str(project.id),
        scan_id=str(scan.id),
        has_certificate=certificate is not None,
        has_public_key=public_key is not None,
        has_attestation=attestation is not None,
        has_attest_cert=attest_cert is not None,
        bytes=buffer.tell(),
    )

    return SignatureArtifact(
        content=buffer.getvalue(),
        filename=f"sbom-signature-{project.slug}.zip",
        media_type="application/zip",
    )


__all__ = [
    "KIND_ATTESTATION",
    "KIND_ATTEST_CERT",
    "KIND_CERTIFICATE",
    "KIND_SBOM",
    "KIND_SIGNATURE",
    "SBOMArtifactTooLarge",
    "SBOMSignatureError",
    "SBOMSignatureNotFound",
    "SignatureArtifact",
    "build_signature_bundle",
    "get_public_key",
    "get_signature_artifact",
]
