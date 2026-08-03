"""
Unit tests for ``services/sbom_signature.py`` — v2.3-s3 (backend).

These are pure-unit tests: the DB loaders are exercised against an in-memory
stand-in (a tiny fake session) so the security-critical logic — the
workspace-confinement file read, the public-key resolution (private key never
read), and the bundle assembly — is covered without requiring PostgreSQL. The
HTTP wiring (auth + IDOR + 404 envelopes) is covered by the integration test
``tests/integration/test_sbom_signature_api.py``.

Security-focused coverage:
  - a ``storage_path`` that escapes the workspace root is rejected (no
    arbitrary-file read), and resolves to a 404 (None) for the caller.
  - a symlink that escapes the workspace is rejected.
  - the public-key reader reads only the ``.pub`` — never the private key.
  - the bundle requires SBOM + signature and never ships private material.
"""

from __future__ import annotations

import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import services.sbom_signature as sig

# ---------------------------------------------------------------------------
# Fakes — a minimal Project + a fake AsyncSession that returns canned rows.
# ---------------------------------------------------------------------------


def _project() -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug="acme-portal",
        name="Acme Portal",
        team_id=uuid.uuid4(),
    )


class _FakeResult:
    def __init__(self, *, scalar=None, first=None):
        self._scalar = scalar
        self._first = first

    def scalar_one_or_none(self):
        return self._scalar

    def first(self):
        return self._first


class _FakeSession:
    """Returns a pre-seeded result for each successive ``execute`` call.

    The service calls ``execute`` in a known order per public function; we seed a
    queue of results and pop them. This keeps the test honest about call order
    without mocking SQLAlchemy internals.
    """

    def __init__(self, results: list[_FakeResult]):
        self._results = list(results)
        self.calls = 0

    async def execute(self, _stmt):  # noqa: ANN001 - statement is opaque here
        self.calls += 1
        if not self._results:
            return _FakeResult()
        return self._results.pop(0)


def _scan() -> Any:
    return SimpleNamespace(id=uuid.uuid4(), status="succeeded")


# ---------------------------------------------------------------------------
# Trust boundary: _read_within_workspace
# ---------------------------------------------------------------------------


# A cap large enough not to interfere with the trust-boundary tests; the size
# guard itself is covered separately below.
_BIG_CAP = 64 * 1024 * 1024


def test_read_within_workspace_reads_file_inside_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    f = tmp_path / "scan" / "sig.sig"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"SIGNATURE-BYTES")

    assert sig._read_within_workspace(str(f), max_bytes=_BIG_CAP) == b"SIGNATURE-BYTES"


def test_read_within_workspace_rejects_path_outside_root(tmp_path, monkeypatch):
    # The workspace is a child dir; a sibling file is OUTSIDE it. A tampered
    # storage_path pointing there must not be readable (arbitrary-file-read).
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(workspace))

    outside = tmp_path / "etc_secret"
    outside.write_bytes(b"TOP-SECRET")

    assert sig._read_within_workspace(str(outside), max_bytes=_BIG_CAP) is None


def test_read_within_workspace_rejects_traversal(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(workspace))

    secret = tmp_path / "passwd"
    secret.write_bytes(b"root:x:0:0")

    # Classic ../ traversal out of the workspace.
    traversal = workspace / ".." / "passwd"
    assert sig._read_within_workspace(str(traversal), max_bytes=_BIG_CAP) is None


def test_read_within_workspace_rejects_symlink_escape(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(workspace))

    secret = tmp_path / "outside_secret"
    secret.write_bytes(b"SECRET")
    # A symlink that LIVES in the workspace but RESOLVES outside it. .resolve()
    # follows it, so the containment check sees the escape and rejects it.
    link = workspace / "link.sig"
    link.symlink_to(secret)

    assert sig._read_within_workspace(str(link), max_bytes=_BIG_CAP) is None


def test_read_within_workspace_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    assert (
        sig._read_within_workspace(str(tmp_path / "does-not-exist.sig"), max_bytes=_BIG_CAP)
        is None
    )


def test_read_within_workspace_directory_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    d = tmp_path / "a-dir"
    d.mkdir()
    assert sig._read_within_workspace(str(d), max_bytes=_BIG_CAP) is None


def test_read_within_workspace_over_cap_raises(tmp_path, monkeypatch):
    # A file larger than the cap is rejected with SBOMArtifactTooLarge (413),
    # never read into memory.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    f = tmp_path / "big.sig"
    f.write_bytes(b"X" * 200)

    with pytest.raises(sig.SBOMArtifactTooLarge):
        sig._read_within_workspace(str(f), max_bytes=100)


# ---------------------------------------------------------------------------
# Public key resolution — only ever the .pub, never the private key.
# ---------------------------------------------------------------------------


def test_read_public_key_reads_pub_file(tmp_path, monkeypatch):
    pub = tmp_path / "cosign.pub"
    pub.write_bytes(b"-----BEGIN PUBLIC KEY-----\nPUB\n-----END PUBLIC KEY-----\n")
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(pub))

    assert sig._read_public_key() == pub.read_bytes()


def test_read_public_key_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)
    assert sig._read_public_key() is None


def test_read_public_key_derives_pub_from_private_key_path(tmp_path, monkeypatch):
    # The config accessor swaps cosign.key -> cosign.pub. The PRIVATE key file
    # exists with secret content; we must NOT read it — only the derived .pub.
    private = tmp_path / "cosign.key"
    private.write_bytes(b"PRIVATE-KEY-SECRET-DO-NOT-LEAK")
    pub = tmp_path / "cosign.pub"
    pub_bytes = b"-----BEGIN PUBLIC KEY-----\nDERIVEDPUB\n-----END PUBLIC KEY-----\n"
    pub.write_bytes(pub_bytes)

    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(private))

    content = sig._read_public_key()
    assert content == pub_bytes
    assert b"PRIVATE-KEY-SECRET" not in (content or b"")


def test_read_public_key_appends_pub_for_non_key_suffix(tmp_path, monkeypatch):
    # A private key path that does NOT end in .key gets <path>.pub appended
    # (last-resort branch of cosign_public_key_path). We still read only the .pub.
    private = tmp_path / "signing-cred"
    private.write_bytes(b"PRIVATE")
    pub = tmp_path / "signing-cred.pub"
    pub_bytes = b"-----BEGIN PUBLIC KEY-----\nAPPENDED\n-----END PUBLIC KEY-----\n"
    pub.write_bytes(pub_bytes)
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(private))
    assert sig._read_public_key() == pub_bytes


def test_read_public_key_missing_pub_returns_none(tmp_path, monkeypatch):
    # private key path set but no .pub on disk -> None (not a crash).
    private = tmp_path / "cosign.key"
    private.write_bytes(b"PRIVATE")
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(private))
    assert sig._read_public_key() is None


def test_get_public_key_returns_artifact(tmp_path, monkeypatch):
    pub = tmp_path / "cosign.pub"
    pub_bytes = b"-----BEGIN PUBLIC KEY-----\nPUBKEY\n-----END PUBLIC KEY-----\n"
    pub.write_bytes(pub_bytes)
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(pub))

    artifact = sig.get_public_key(project=_project())
    assert artifact is not None
    assert artifact.content == pub_bytes
    assert artifact.filename == "cosign.pub"
    assert artifact.media_type == "application/x-pem-file"


def test_get_public_key_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)
    assert sig.get_public_key(project=_project()) is None


# ---------------------------------------------------------------------------
# get_signature_artifact — DB locator + trust-boundary read.
# ---------------------------------------------------------------------------


async def test_get_signature_artifact_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    f = tmp_path / "sbom.cdx.json.sig"
    f.write_bytes(b"SIG")

    scan = _scan()
    session = _FakeSession(
        [
            _FakeResult(scalar=scan),  # latest succeeded scan
            _FakeResult(first=(str(f), "abc123", f.stat().st_size)),  # (path, sha256, byte_size)
        ]
    )
    project = _project()

    artifact = await sig.get_signature_artifact(session, project=project, kind=sig.KIND_SIGNATURE)
    assert artifact is not None
    assert artifact.content == b"SIG"
    assert artifact.filename == "sbom-acme-portal.cdx.json.sig"
    assert artifact.media_type == "application/octet-stream"
    assert artifact.sha256 == "abc123"


async def test_get_signature_artifact_no_scan_returns_none():
    session = _FakeSession([_FakeResult(scalar=None)])  # no succeeded scan
    artifact = await sig.get_signature_artifact(
        session, project=_project(), kind=sig.KIND_SIGNATURE
    )
    assert artifact is None


async def test_get_signature_artifact_no_artifact_row_returns_none():
    session = _FakeSession(
        [
            _FakeResult(scalar=_scan()),  # scan exists
            _FakeResult(first=None),  # but no signature artifact row
        ]
    )
    artifact = await sig.get_signature_artifact(
        session, project=_project(), kind=sig.KIND_SIGNATURE
    )
    assert artifact is None


async def test_get_signature_artifact_path_escape_returns_none(tmp_path, monkeypatch):
    # Artifact row exists but its storage_path points OUTSIDE the workspace —
    # the trust boundary rejects it and the caller sees None (-> 404).
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(workspace))
    outside = tmp_path / "secret"
    outside.write_bytes(b"SECRET")

    session = _FakeSession(
        [
            _FakeResult(scalar=_scan()),
            _FakeResult(first=(str(outside), None, None)),
        ]
    )
    artifact = await sig.get_signature_artifact(
        session, project=_project(), kind=sig.KIND_SIGNATURE
    )
    assert artifact is None


# ---------------------------------------------------------------------------
# Bundle assembly.
# ---------------------------------------------------------------------------


def _seed_bundle_session(
    tmp_path: Path,
    *,
    with_cert: bool = False,
    with_attestation: bool = False,
    with_attest_cert: bool = False,
) -> tuple[_FakeSession, Any]:
    """Seed a fake session whose execute() queue matches build_signature_bundle.

    Call order inside build_signature_bundle:
      1. _load_latest_succeeded_scan  (scalar)
      2. get_signature_artifact(SBOM): scan(scalar) + artifact(first)
      3. get_signature_artifact(SIG):  scan(scalar) + artifact(first)
      4. get_signature_artifact(CERT): scan(scalar) + artifact(first)
      5. get_signature_artifact(ATTESTATION): scan(scalar) + artifact(first)
      6. get_signature_artifact(ATTEST_CERT): scan(scalar) + artifact(first)
         — ONLY when an attestation was found (the service short-circuits).
    (public key is read off disk, not via the session.)

    Artifact rows are 3-tuples ``(storage_path, sha256, byte_size)``.
    """
    scan = _scan()
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_bytes(b'{"bomFormat":"CycloneDX"}')
    sigf = tmp_path / "sbom.cdx.json.sig"
    sigf.write_bytes(b"SIGNATURE")
    cert = tmp_path / "sbom.cdx.json.cert"
    cert.write_bytes(b"-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE-----\n")
    attest = tmp_path / "sbom.intoto.jsonl"
    attest.write_bytes(b'{"payloadType":"application/vnd.in-toto+json"}')
    attest_cert = tmp_path / "sbom.attest.cert"
    attest_cert.write_bytes(b"-----BEGIN CERTIFICATE-----\nAC\n-----END CERTIFICATE-----\n")

    def _row(path: Path, present: bool):  # noqa: ANN202 - tiny local helper
        return (str(path), None, path.stat().st_size) if present else None

    results = [
        _FakeResult(scalar=scan),  # 1. latest scan
        _FakeResult(scalar=scan),  # 2a SBOM scan
        _FakeResult(first=(str(sbom), None, sbom.stat().st_size)),  # 2b SBOM artifact
        _FakeResult(scalar=scan),  # 3a SIG scan
        _FakeResult(first=(str(sigf), "deadbeef", sigf.stat().st_size)),  # 3b SIG artifact
        _FakeResult(scalar=scan),  # 4a CERT scan
        _FakeResult(first=_row(cert, with_cert)),  # 4b CERT artifact
        _FakeResult(scalar=scan),  # 5a ATTEST scan
        _FakeResult(first=_row(attest, with_attestation)),  # 5b ATTEST
    ]
    # The service only fetches the attestation cert when an attestation exists.
    if with_attestation:
        results += [
            _FakeResult(scalar=scan),  # 6a ATTEST_CERT scan
            _FakeResult(first=_row(attest_cert, with_attest_cert)),  # 6b ATTEST_CERT
        ]
    return _FakeSession(results), _project()


async def test_build_bundle_minimal_sbom_and_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)

    session, project = _seed_bundle_session(tmp_path)
    artifact = await sig.build_signature_bundle(session, project=project)
    assert artifact is not None
    assert artifact.media_type == "application/zip"
    assert artifact.filename == "sbom-signature-acme-portal.zip"

    with zipfile.ZipFile(BytesIO(artifact.content)) as zf:
        names = set(zf.namelist())
    assert "sbom-acme-portal.cdx.json" in names
    assert "sbom-acme-portal.cdx.json.sig" in names
    assert "VERIFY.md" in names
    # No cosign key / cert configured -> keyless-style README, no public key.
    assert "cosign.pub" not in names


async def test_build_bundle_with_public_key_cert_and_attestation(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    pub = tmp_path / "cosign.pub"
    pub_bytes = b"-----BEGIN PUBLIC KEY-----\nPUBKEY\n-----END PUBLIC KEY-----\n"
    pub.write_bytes(pub_bytes)
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(pub))

    session, project = _seed_bundle_session(tmp_path, with_cert=True, with_attestation=True)
    artifact = await sig.build_signature_bundle(session, project=project)
    assert artifact is not None

    with zipfile.ZipFile(BytesIO(artifact.content)) as zf:
        names = set(zf.namelist())
        readme = zf.read("VERIFY.md").decode()
        # The public key is included and is exactly the .pub bytes.
        assert zf.read("cosign.pub") == pub_bytes

    assert "sbom-acme-portal.cdx.json" in names
    assert "sbom-acme-portal.cdx.json.sig" in names
    assert "sbom-acme-portal.cdx.json.cert.pem" in names
    assert "sbom-acme-portal.intoto.jsonl" in names
    assert "cosign.pub" in names
    # Key-based README mentions verify-blob --key; private material never present.
    assert "cosign verify-blob" in readme
    assert "--key cosign.pub" in readme
    # No private key bytes anywhere in the archive.
    assert b"PRIVATE" not in artifact.content


async def test_build_bundle_no_scan_returns_none():
    session = _FakeSession([_FakeResult(scalar=None)])
    assert await sig.build_signature_bundle(session, project=_project()) is None


async def test_build_bundle_unsigned_scan_returns_none(tmp_path, monkeypatch):
    # Scan exists + SBOM exists, but NO signature artifact -> no bundle.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_bytes(b"{}")
    scan = _scan()
    session = _FakeSession(
        [
            _FakeResult(scalar=scan),  # latest scan
            _FakeResult(scalar=scan),  # SBOM scan
            _FakeResult(first=(str(sbom), None, sbom.stat().st_size)),  # SBOM artifact present
            _FakeResult(scalar=scan),  # SIG scan
            _FakeResult(first=None),  # SIG artifact ABSENT
        ]
    )
    assert await sig.build_signature_bundle(session, project=_project()) is None


async def test_build_bundle_includes_keyless_attestation_cert(tmp_path, monkeypatch):
    # [Info fix] keyless attestation -> bundle must carry sbom-*.attest.cert.pem so
    # a consumer can run `cosign verify-blob-attestation` from the bundle alone.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)  # keyless -> cert path, no pub

    session, project = _seed_bundle_session(
        tmp_path, with_cert=True, with_attestation=True, with_attest_cert=True
    )
    artifact = await sig.build_signature_bundle(session, project=project)
    assert artifact is not None

    with zipfile.ZipFile(BytesIO(artifact.content)) as zf:
        names = set(zf.namelist())
        readme = zf.read("VERIFY.md").decode()
    assert "sbom-acme-portal.attest.cert.pem" in names
    assert "sbom-acme-portal.intoto.jsonl" in names
    # README points keyless consumers at verify-blob-attestation with the cert.
    assert "cosign verify-blob-attestation" in readme
    assert "sbom-*.attest.cert.pem" in readme


async def test_build_bundle_no_attest_cert_when_no_attestation(tmp_path, monkeypatch):
    # No attestation -> the service must NOT even query for the attest cert
    # (short-circuit). _seed_bundle_session reflects that by omitting the rows;
    # the bundle still builds without an attest cert member.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)

    session, project = _seed_bundle_session(tmp_path, with_cert=True, with_attestation=False)
    artifact = await sig.build_signature_bundle(session, project=project)
    assert artifact is not None
    with zipfile.ZipFile(BytesIO(artifact.content)) as zf:
        names = set(zf.namelist())
    assert not any(n.endswith(".attest.cert.pem") for n in names)


# ---------------------------------------------------------------------------
# [Medium fix] public-key reader refuses to serve private material.
# ---------------------------------------------------------------------------


def test_read_public_key_refuses_private_key(tmp_path, monkeypatch):
    # Misconfiguration: COSIGN_PUBLIC_KEY_PATH points at an actual PRIVATE key
    # (or a .pub symlink that targets it). The reader must REFUSE: None -> 404,
    # so the private key is NEVER served to a developer.
    bad_pub = tmp_path / "cosign.pub"
    bad_pub.write_bytes(
        b"-----BEGIN PRIVATE KEY-----\nSECRET-PRIVATE-BYTES\n-----END PRIVATE KEY-----\n"
    )
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(bad_pub))

    assert sig._read_public_key() is None
    # And the public-facing accessor returns None too (router maps it to 404).
    assert sig.get_public_key(project=_project()) is None


def test_read_public_key_refuses_encrypted_private_key(tmp_path, monkeypatch):
    bad_pub = tmp_path / "cosign.pub"
    bad_pub.write_bytes(
        b"-----BEGIN ENCRYPTED PRIVATE KEY-----\nX\n-----END ENCRYPTED PRIVATE KEY-----\n"
    )
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(bad_pub))
    assert sig._read_public_key() is None


def test_read_public_key_refuses_non_public_material(tmp_path, monkeypatch):
    # A blob that is neither a PUBLIC KEY nor a CERTIFICATE is refused (the
    # operator pointed the path at the wrong file).
    bad_pub = tmp_path / "cosign.pub"
    bad_pub.write_bytes(b"just some random bytes, no recognizable pem header")
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(bad_pub))
    assert sig._read_public_key() is None


def test_read_public_key_accepts_certificate(tmp_path, monkeypatch):
    # An x509 CERTIFICATE is acceptable public material.
    pub = tmp_path / "cosign.pub"
    pub.write_bytes(b"-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE-----\n")
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(pub))
    assert sig._read_public_key() == pub.read_bytes()


def test_read_public_key_via_symlink_to_private_key_refused(tmp_path, monkeypatch):
    # Defense against a .pub symlink that resolves to the private key file.
    private = tmp_path / "cosign.key"
    private.write_bytes(
        b"-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----\n"
    )
    pub = tmp_path / "cosign.pub"
    pub.symlink_to(private)
    monkeypatch.setenv("COSIGN_PUBLIC_KEY_PATH", str(pub))
    assert sig._read_public_key() is None


# ---------------------------------------------------------------------------
# [Low fix] size cap (DoS) — get_signature_artifact + bundle total.
# ---------------------------------------------------------------------------


async def test_get_signature_artifact_over_cap_via_db_size_raises(tmp_path, monkeypatch):
    # The persisted byte_size already exceeds the cap -> 413 BEFORE reading disk.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("SBOM_DOWNLOAD_MAX_BYTES", "10")
    f = tmp_path / "sbom.cdx.json.sig"
    f.write_bytes(b"SIG")  # tiny on disk; the DB row LIES about being huge

    session = _FakeSession(
        [
            _FakeResult(scalar=_scan()),
            _FakeResult(first=(str(f), "abc", 1_000_000)),  # byte_size >> cap
        ]
    )
    with pytest.raises(sig.SBOMArtifactTooLarge):
        await sig.get_signature_artifact(session, project=_project(), kind=sig.KIND_SIGNATURE)


async def test_get_signature_artifact_over_cap_via_disk_size_raises(tmp_path, monkeypatch):
    # byte_size is absent (older row) but the on-disk file exceeds the cap ->
    # the authoritative stat() guard fires.
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("SBOM_DOWNLOAD_MAX_BYTES", "10")
    f = tmp_path / "sbom.cdx.json.sig"
    f.write_bytes(b"X" * 100)

    session = _FakeSession(
        [
            _FakeResult(scalar=_scan()),
            _FakeResult(first=(str(f), None, None)),  # no byte_size -> rely on disk stat
        ]
    )
    with pytest.raises(sig.SBOMArtifactTooLarge):
        await sig.get_signature_artifact(session, project=_project(), kind=sig.KIND_SIGNATURE)


async def test_build_bundle_over_cap_total_raises(tmp_path, monkeypatch):
    # Each member is UNDER the per-file cap, but the running TOTAL exceeds it —
    # this exercises the bundle-total guard (distinct from the per-file guard).
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)
    # SBOM is 25 bytes, signature is 9 bytes (see _seed_bundle_session). A cap of
    # 30 admits each member individually but rejects the 34-byte total.
    monkeypatch.setenv("SBOM_DOWNLOAD_MAX_BYTES", "30")

    session, project = _seed_bundle_session(tmp_path)
    with pytest.raises(sig.SBOMArtifactTooLarge):
        await sig.build_signature_bundle(session, project=project)


# ---------------------------------------------------------------------------
# [Info fix] VERIFY.md sanitizes the free-text project.name.
# ---------------------------------------------------------------------------


async def test_verification_readme_sanitizes_project_name(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)

    session, project = _seed_bundle_session(tmp_path)
    # Inject newlines / control chars into the heading; slug stays safe.
    project.name = "Evil\nProject\r\x00Name"

    artifact = await sig.build_signature_bundle(session, project=project)
    assert artifact is not None
    with zipfile.ZipFile(BytesIO(artifact.content)) as zf:
        readme = zf.read("VERIFY.md").decode()

    heading = readme.splitlines()[0]
    # The whole name collapses onto the single heading line — no forged lines,
    # no embedded NUL / CR.
    assert heading == "# Verifying the SBOM signature for project: EvilProjectName"
    assert "\x00" not in readme
