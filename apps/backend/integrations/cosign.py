"""
cosign adapter — SBOM signing (v2.3-s1).

After a source scan generates the CycloneDX SBOM (``integrations/cdxgen.py``),
this adapter signs the SBOM bytes with `cosign <https://github.com/sigstore/cosign>`_
so a downstream consumer can verify the artifact's integrity + provenance.

D2 decision (confirmed)
-----------------------
KEY-BASED signing is the DEFAULT — self-hosted / on-prem / air-gapped is the
first-class target, where there is no ambient OIDC identity to lean on. KEYLESS
(OIDC; sigstore Fulcio short-lived certs + Rekor transparency log) is an opt-in
alternative enabled via ``COSIGN_KEYLESS=true``. When the toggle is unset we run
key-based; if key-based is requested but no key is configured we SKIP signing
(best-effort) rather than fall through to keyless implicitly.

Best-effort contract (CLAUDE.md core rule #3 + scan-pipeline philosophy)
------------------------------------------------------------------------
cosign runs inside the Celery worker (never on the synchronous API path). The
ONLY public entry point, :func:`sign_blob`, NEVER raises into the scan: a
missing binary, an unconfigured key, a malformed encrypted password, or a cosign
non-zero exit all return :data:`SigningSkipped`-style ``None``-bearing results
with a structured WARNING. An unsigned SBOM is a degraded-but-non-fatal outcome,
mirroring the scancode / preserve stages. The caller (``tasks/scan_source.py``)
persists whatever signature artifacts it gets back and moves on.

Security
--------
- The private-key PASSWORD is decrypted (``core.crypto`` Fernet) ONLY at signing
  time and handed to cosign via the ``COSIGN_PASSWORD`` *subprocess env var* —
  never on argv (argv is world-readable via ``/proc/<pid>/cmdline``). The
  plaintext is never passed to any logging call: it lives only in the subprocess
  ``env`` dict and the local variable that fills it. cosign's stderr is
  additionally secret-scrubbed (``_scrub_secret_text`` + ``mask_pii``) before it
  reaches the structured log, so a future cosign version / plugin that echoed a
  token (``COSIGN_PASSWORD`` / ``SIGSTORE_ID_TOKEN`` / a bearer JWT) would still
  be redacted rather than leaked.
- Subprocess invocation uses a fixed argument LIST (no ``shell=True``), so an
  attacker-controlled file path cannot inject a shell command. We additionally
  reject path arguments that do not resolve to an existing regular file before
  spawning, so a crafted ``--`` / option-looking path cannot smuggle a flag.
- Trust assumption for ``blob_path``: it MUST be a worker-generated workspace
  path (the SBOM written by the cdxgen stage), never an attacker-named one. As a
  defense-in-depth control we reject a ``blob_path`` that is a symlink (only the
  blob; an operator-configured KEY path may legitimately be a mounted-secret
  symlink), closing a symlink-swap window on the one input whose name could in
  principle be influenced upstream.
- The signing env is the scrubbed base allowlist (PATH / HOME / proxy / CA hints)
  plus only the cosign-specific keys we set — worker secrets (DT_API_KEY /
  SECRET_KEY / DATABASE_URL) are stripped so a future cosign plugin / telemetry
  path has nothing to exfiltrate.
- A ``mock`` backend (``TRUSTEDOSS_SCAN_BACKEND=mock``) writes deterministic
  fixture signature bytes without invoking cosign — used by unit tests and the
  smoke harness, the same pivot cdxgen / scancode offer.

Outputs (key-based)
-------------------
``cosign sign-blob --key <key> --output-signature <sig> <blob>`` produces a
detached signature file. We persist it as the ``sbom_cyclonedx_sig`` artifact.

Outputs (keyless)
-----------------
``cosign sign-blob --yes --output-signature <sig> --output-certificate <cert>
<blob>`` additionally emits the Fulcio-issued signing certificate. We persist the
cert as ``sbom_cyclonedx_cert`` alongside the signature.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # noqa: S404 — running a vetted local binary, not user input
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from core.config import (
    cosign_key_password_encrypted,
    cosign_key_path,
    cosign_keyless,
    cosign_timeout_seconds,
    scan_backend_mode,
)
from core.crypto import SecretDecryptionError, SecretEncryptionError, decrypt_secret
from core.pii_mask import mask_pii
from integrations._subprocess_env import scrubbed_env_for_cosign

log = structlog.get_logger("integrations.cosign")


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignResult:
    """Outcome of a :func:`sign_blob` call.

    ``signed`` is the single boolean the caller checks. When ``False`` the scan
    proceeds unsigned and ``skip_reason`` carries a short, secret-free hint for
    the structured log / audit trail. When ``True``:
      - ``signature_path`` always points at the detached signature file.
      - ``certificate_path`` is set only for the keyless flow (Fulcio cert).
      - ``mode`` is ``"key"`` or ``"keyless"``.
    """

    signed: bool
    mode: str | None = None
    signature_path: Path | None = None
    certificate_path: Path | None = None
    skip_reason: str | None = None


@dataclass(frozen=True)
class AttestResult:
    """Outcome of an :func:`attest_blob` call (v2.3-s2).

    ``attested`` is the single boolean the caller checks. When ``False`` the scan
    proceeds without an attestation and ``skip_reason`` carries a short,
    secret-free hint. When ``True``:
      - ``attestation_path`` points at the in-toto attestation (DSSE envelope)
        file cosign wrote.
      - ``certificate_path`` is set only for the keyless flow (Fulcio cert).
      - ``mode`` is ``"key"`` or ``"keyless"``.

    The contract mirrors :class:`SignResult`: :func:`attest_blob` NEVER raises
    into the scan. An un-attested SBOM is degraded-but-non-fatal, exactly like
    an unsigned one.
    """

    attested: bool
    mode: str | None = None
    attestation_path: Path | None = None
    certificate_path: Path | None = None
    skip_reason: str | None = None


# Stable skip-reason tokens (secret-free, log/audit friendly).
_SKIP_NOT_INSTALLED = "cosign_not_installed"
_SKIP_NO_KEY = "key_based_no_key_configured"
_SKIP_BAD_PASSWORD = "key_password_decrypt_failed"  # noqa: S105 — log token, not a secret
# Distinct from _SKIP_BAD_PASSWORD: the password could not even be *resolved*
# because the encryption key itself is misconfigured (e.g. prod fail-closed —
# GITHUB_APP_ENCRYPTION_KEY unset in production raises SecretEncryptionError).
_SKIP_KEY_CONFIG = "key_encryption_misconfigured"  # noqa: S105 — log token, not a secret
_SKIP_BLOB_MISSING = "blob_path_not_a_regular_file"
_SKIP_KEY_MISSING = "key_path_not_a_regular_file"
_SKIP_TIMEOUT = "cosign_timeout"
_SKIP_FAILED = "cosign_nonzero_exit"
# v2.3-s2 attestation-specific: the predicate JSON could not be written to the
# workspace before invoking cosign (an unexpected I/O error). Distinct from
# _SKIP_FAILED so the log/audit can tell a predicate-write failure from a cosign
# non-zero exit.
_SKIP_PREDICATE_WRITE = "predicate_write_failed"
# v2.3-s2 security hardening: a keyless flow produced a signature / attestation but
# cosign emitted NO Fulcio certificate. In the keyless trust model the short-lived
# Fulcio cert IS the verifiable identity (there is no operator-held public key), so
# a missing cert means a downstream consumer cannot establish provenance. We must
# NOT report success in that case — treat it as a skip rather than silently dropping
# the trust anchor. Key-based flows are unaffected (their trust anchor is the key).
_SKIP_NO_CERTIFICATE = "keyless_certificate_missing"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sign_blob(
    *,
    blob_path: Path,
    output_dir: Path,
    keyless: bool | None = None,
    backend: str | None = None,
    timeout_seconds: int | None = None,
) -> SignResult:
    """Sign ``blob_path`` with cosign, writing artifacts under ``output_dir``.

    NEVER raises into the scan — every failure mode returns a
    ``SignResult(signed=False, skip_reason=...)`` with a WARNING. This is the
    only function the scan pipeline calls.

    Trust assumption: ``blob_path`` MUST be a worker-generated workspace path
    (the SBOM emitted by the cdxgen stage), not an attacker-named path. We reject
    a symlinked ``blob_path`` as defense-in-depth, but callers must not point this
    at untrusted, attacker-controlled filenames.

    Args:
        blob_path: The file to sign (the CycloneDX SBOM JSON). Must be a regular,
            non-symlink, worker-generated file.
        output_dir: Workspace subdirectory for the signature / certificate.
        keyless: Override ``cosign_keyless()`` (the ``COSIGN_KEYLESS`` toggle).
        backend: Override ``scan_backend_mode()`` (``mock`` writes fixtures).
        timeout_seconds: Override ``cosign_timeout_seconds()``.
    """
    mode_backend = (backend or scan_backend_mode()).lower()
    use_keyless = cosign_keyless() if keyless is None else keyless

    # Validate the blob is a real, existing regular file (NOT a symlink) BEFORE
    # we let it near a subprocess argv — a crafted "path" that begins with ``-``
    # or is a symlink must not become a cosign flag, a symlink-swap target, or a
    # confusing error. See _is_regular_blob + the sign_blob trust note above.
    if not _is_regular_blob(blob_path):
        log.warning("cosign_sign_skipped", reason=_SKIP_BLOB_MISSING, blob=str(blob_path))
        return SignResult(signed=False, skip_reason=_SKIP_BLOB_MISSING)

    output_dir.mkdir(parents=True, exist_ok=True)
    signature_path = output_dir / "sbom.cdx.json.sig"
    certificate_path = output_dir / "sbom.cdx.json.cert"

    if mode_backend == "mock":
        return _write_mock_signature(
            blob_path=blob_path,
            signature_path=signature_path,
            certificate_path=certificate_path,
            keyless=use_keyless,
        )

    if shutil.which("cosign") is None:
        log.warning("cosign_sign_skipped", reason=_SKIP_NOT_INSTALLED)
        return SignResult(signed=False, skip_reason=_SKIP_NOT_INSTALLED)

    timeout = timeout_seconds if timeout_seconds is not None else cosign_timeout_seconds()

    if use_keyless:
        return _sign_keyless(
            blob_path=blob_path,
            signature_path=signature_path,
            certificate_path=certificate_path,
            timeout=timeout,
        )
    return _sign_key_based(
        blob_path=blob_path,
        signature_path=signature_path,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Attestation (v2.3-s2) — in-toto / SLSA provenance over the SBOM
# ---------------------------------------------------------------------------


def attest_blob(
    *,
    blob_path: Path,
    predicate: dict[str, Any],
    predicate_type: str,
    output_dir: Path,
    keyless: bool | None = None,
    backend: str | None = None,
    timeout_seconds: int | None = None,
) -> AttestResult:
    """Attest ``blob_path`` with cosign, embedding ``predicate`` as an in-toto
    Statement signed into a DSSE envelope (v2.3-s2).

    Mirrors :func:`sign_blob`'s contract exactly — it NEVER raises into the
    scan. Every failure mode (missing binary, unconfigured key, predicate-write
    error, decrypt failure, cosign non-zero / timeout) returns an
    ``AttestResult(attested=False, skip_reason=...)`` with a WARNING. An
    un-attested SBOM is degraded-but-non-fatal.

    Reuses the s1 helpers (``_is_regular_blob``, ``_resolve_key_password``,
    ``scrubbed_env_for_cosign``, the argv-safety ``--`` sentinel, the secret
    scrubber) so the security posture is identical to signing.

    Args:
        blob_path: The SBOM file the attestation is *about* (the subject). Same
            trust assumption as :func:`sign_blob`: a worker-generated,
            non-symlink workspace artifact.
        predicate: The (already-built) SLSA provenance predicate dict. cosign
            wraps it in an in-toto Statement keyed to the blob's digest and
            signs the DSSE envelope. The predicate must contain NO secrets — see
            ``integrations.attestation``.
        predicate_type: The in-toto predicateType URI cosign records (e.g.
            ``https://slsa.dev/provenance/v1``). cosign 2.x accepts a custom URI
            via ``--type``.
        output_dir: Workspace subdirectory for the predicate file + attestation.
        keyless / backend / timeout_seconds: Same overrides as :func:`sign_blob`.
    """
    mode_backend = (backend or scan_backend_mode()).lower()
    use_keyless = cosign_keyless() if keyless is None else keyless

    # Same pre-spawn argv-safety + symlink-swap guard as sign_blob.
    if not _is_regular_blob(blob_path):
        log.warning("cosign_attest_skipped", reason=_SKIP_BLOB_MISSING, blob=str(blob_path))
        return AttestResult(attested=False, skip_reason=_SKIP_BLOB_MISSING)

    output_dir.mkdir(parents=True, exist_ok=True)
    predicate_path = output_dir / "sbom.predicate.json"
    attestation_path = output_dir / "sbom.intoto.jsonl"
    certificate_path = output_dir / "sbom.attest.cert"

    # Serialize the predicate to disk first — cosign reads it via --predicate.
    # A write failure is its own skip token (distinct from a cosign exit).
    try:
        predicate_path.write_text(
            json.dumps(predicate, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("cosign_attest_skipped", reason=_SKIP_PREDICATE_WRITE, detail=str(exc)[:200])
        return AttestResult(attested=False, skip_reason=_SKIP_PREDICATE_WRITE)

    if mode_backend == "mock":
        return _write_mock_attestation(
            predicate_path=predicate_path,
            attestation_path=attestation_path,
            certificate_path=certificate_path,
            keyless=use_keyless,
        )

    if shutil.which("cosign") is None:
        log.warning("cosign_attest_skipped", reason=_SKIP_NOT_INSTALLED)
        return AttestResult(attested=False, skip_reason=_SKIP_NOT_INSTALLED)

    timeout = timeout_seconds if timeout_seconds is not None else cosign_timeout_seconds()

    if use_keyless:
        return _attest_keyless(
            blob_path=blob_path,
            predicate_path=predicate_path,
            predicate_type=predicate_type,
            attestation_path=attestation_path,
            certificate_path=certificate_path,
            timeout=timeout,
        )
    return _attest_key_based(
        blob_path=blob_path,
        predicate_path=predicate_path,
        predicate_type=predicate_type,
        attestation_path=attestation_path,
        timeout=timeout,
    )


def _attest_key_based(
    *,
    blob_path: Path,
    predicate_path: Path,
    predicate_type: str,
    attestation_path: Path,
    timeout: int,
) -> AttestResult:
    """Key-based ``cosign attest-blob --key <key>`` flow (the D2 default).

    Resolves the key + password exactly like :func:`_sign_key_based` (password
    via COSIGN_PASSWORD env, never argv) and reuses the same graceful-skip
    branches so the attestation path honours the "NEVER raises" contract.
    """
    key_path_raw = cosign_key_path()
    if key_path_raw is None:
        log.warning("cosign_attest_skipped", reason=_SKIP_NO_KEY)
        return AttestResult(attested=False, skip_reason=_SKIP_NO_KEY)

    key_path = Path(key_path_raw)
    if not _is_regular_file(key_path):
        log.warning("cosign_attest_skipped", reason=_SKIP_KEY_MISSING)
        return AttestResult(attested=False, skip_reason=_SKIP_KEY_MISSING)

    try:
        password = _resolve_key_password()
    except SecretDecryptionError:
        log.warning("cosign_attest_skipped", reason=_SKIP_BAD_PASSWORD)
        return AttestResult(attested=False, skip_reason=_SKIP_BAD_PASSWORD)
    except SecretEncryptionError:
        log.warning("cosign_attest_skipped", reason=_SKIP_KEY_CONFIG)
        return AttestResult(attested=False, skip_reason=_SKIP_KEY_CONFIG)

    env = scrubbed_env_for_cosign()
    env["COSIGN_PASSWORD"] = password

    cmd = [
        "cosign",
        "attest-blob",
        "--yes",
        # cosign v3 changed the defaults: sign-blob/attest-blob emit the new
        # bundle format and consult a signing config unless told otherwise.
        # Our verify path (and stored artifacts) expect the v2-style detached
        # signature/attestation files, so pin the old behaviour explicitly.
        "--new-bundle-format=false",
        "--use-signing-config=false",
        "--key",
        str(key_path),
        "--predicate",
        str(predicate_path),
        "--type",
        predicate_type,
        "--output-attestation",
        str(attestation_path),
        # Blob validated as a regular file above; after ``--`` so a path that
        # looks like an option cannot be parsed as one.
        "--",
        str(blob_path),
    ]
    log.info("cosign_attest_start", mode="key", blob=str(blob_path))
    completed = _run_cosign(cmd, env=env, timeout=timeout)
    if completed is None:
        return AttestResult(attested=False, skip_reason=_SKIP_TIMEOUT)
    if completed.returncode != 0:
        log.warning(
            "cosign_attest_skipped",
            reason=_SKIP_FAILED,
            mode="key",
            returncode=completed.returncode,
            stderr=_safe_stderr(completed.stderr),
        )
        return AttestResult(attested=False, skip_reason=_SKIP_FAILED)
    if not attestation_path.exists():
        log.warning(
            "cosign_attest_skipped", reason=_SKIP_FAILED, mode="key", detail="no attestation"
        )
        return AttestResult(attested=False, skip_reason=_SKIP_FAILED)

    log.info("cosign_attest_succeeded", mode="key", attestation=str(attestation_path))
    return AttestResult(attested=True, mode="key", attestation_path=attestation_path)


def _attest_keyless(
    *,
    blob_path: Path,
    predicate_path: Path,
    predicate_type: str,
    attestation_path: Path,
    certificate_path: Path,
    timeout: int,
) -> AttestResult:
    """Keyless ``cosign attest-blob --yes`` (Fulcio/Rekor) flow — opt-in.

    cosign drives its own OIDC identity and emits the Fulcio signing certificate
    alongside the attestation. No private key / password is involved, so no
    Fernet decrypt happens on this path (mirrors :func:`_sign_keyless`).
    """
    env = scrubbed_env_for_cosign()
    cmd = [
        "cosign",
        "attest-blob",
        "--yes",
        # cosign v3 changed the defaults: sign-blob/attest-blob emit the new
        # bundle format and consult a signing config unless told otherwise.
        # Our verify path (and stored artifacts) expect the v2-style detached
        # signature/attestation files, so pin the old behaviour explicitly.
        "--new-bundle-format=false",
        "--use-signing-config=false",
        "--predicate",
        str(predicate_path),
        "--type",
        predicate_type,
        "--output-attestation",
        str(attestation_path),
        "--output-certificate",
        str(certificate_path),
        "--",
        str(blob_path),
    ]
    log.info("cosign_attest_start", mode="keyless", blob=str(blob_path))
    completed = _run_cosign(cmd, env=env, timeout=timeout)
    if completed is None:
        return AttestResult(attested=False, skip_reason=_SKIP_TIMEOUT)
    if completed.returncode != 0:
        log.warning(
            "cosign_attest_skipped",
            reason=_SKIP_FAILED,
            mode="keyless",
            returncode=completed.returncode,
            stderr=_safe_stderr(completed.stderr),
        )
        return AttestResult(attested=False, skip_reason=_SKIP_FAILED)
    if not attestation_path.exists():
        log.warning(
            "cosign_attest_skipped", reason=_SKIP_FAILED, mode="keyless", detail="no attestation"
        )
        return AttestResult(attested=False, skip_reason=_SKIP_FAILED)

    # Keyless trust anchor: the Fulcio cert IS the verifiable identity (there is no
    # operator-held public key on this path). If cosign exited 0 but emitted no
    # certificate, a downstream consumer cannot establish provenance — so we must
    # NOT report success. Skip (WARNING) rather than silently drop the trust root.
    if not certificate_path.exists():
        log.warning(
            "cosign_attest_skipped",
            reason=_SKIP_NO_CERTIFICATE,
            mode="keyless",
            detail="cosign exited 0 but emitted no Fulcio certificate",
        )
        return AttestResult(attested=False, skip_reason=_SKIP_NO_CERTIFICATE)

    log.info(
        "cosign_attest_succeeded",
        mode="keyless",
        attestation=str(attestation_path),
        certificate=str(certificate_path),
    )
    return AttestResult(
        attested=True,
        mode="keyless",
        attestation_path=attestation_path,
        certificate_path=certificate_path,
    )


# ---------------------------------------------------------------------------
# Key-based
# ---------------------------------------------------------------------------


def _sign_key_based(
    *,
    blob_path: Path,
    signature_path: Path,
    timeout: int,
) -> SignResult:
    """Key-based ``cosign sign-blob --key <key>`` flow (the D2 default)."""
    key_path_raw = cosign_key_path()
    if key_path_raw is None:
        log.warning("cosign_sign_skipped", reason=_SKIP_NO_KEY)
        return SignResult(signed=False, skip_reason=_SKIP_NO_KEY)

    key_path = Path(key_path_raw)
    if not _is_regular_file(key_path):
        # The key file is operator-configured, not attacker-controlled, but a
        # typo / unmounted volume must degrade gracefully, not crash the scan.
        log.warning("cosign_sign_skipped", reason=_SKIP_KEY_MISSING)
        return SignResult(signed=False, skip_reason=_SKIP_KEY_MISSING)

    # Resolve the key password (Fernet-decrypted) into the subprocess env. Two
    # non-fatal failure modes are caught here so sign_blob honours its "NEVER
    # raises into the scan" contract:
    #   - SecretDecryptionError: rotated key / corrupt ciphertext — the stored
    #     ciphertext can't be decrypted.
    #   - SecretEncryptionError: the encryption KEY itself is misconfigured.
    #     Notably prod fail-closed: APP_ENV=prod with GITHUB_APP_ENCRYPTION_KEY
    #     unset raises this from core.crypto rather than deriving from SECRET_KEY.
    # Either way we skip signing rather than break the scan; the message carries
    # NO secret bytes (only a stable, secret-free skip token).
    try:
        password = _resolve_key_password()
    except SecretDecryptionError:
        log.warning("cosign_sign_skipped", reason=_SKIP_BAD_PASSWORD)
        return SignResult(signed=False, skip_reason=_SKIP_BAD_PASSWORD)
    except SecretEncryptionError:
        log.warning("cosign_sign_skipped", reason=_SKIP_KEY_CONFIG)
        return SignResult(signed=False, skip_reason=_SKIP_KEY_CONFIG)

    env = scrubbed_env_for_cosign()
    # COSIGN_PASSWORD via ENV, never argv. cosign reads it for the encrypted key.
    # An empty string is the correct value for a passwordless key.
    env["COSIGN_PASSWORD"] = password

    cmd = [
        "cosign",
        "sign-blob",
        "--yes",  # non-interactive: do not prompt to confirm
        # cosign v3 changed the defaults: sign-blob/attest-blob emit the new
        # bundle format and consult a signing config unless told otherwise.
        # Our verify path (and stored artifacts) expect the v2-style detached
        # signature/attestation files, so pin the old behaviour explicitly.
        "--new-bundle-format=false",
        "--use-signing-config=false",
        "--key",
        str(key_path),
        "--output-signature",
        str(signature_path),
        # The blob path is validated as a regular file above; pass it after a
        # ``--`` so a (hypothetical) path that looks like an option cannot be
        # parsed as one.
        "--",
        str(blob_path),
    ]
    log.info("cosign_sign_start", mode="key", blob=str(blob_path))
    completed = _run_cosign(cmd, env=env, timeout=timeout)
    if completed is None:
        # _run_cosign already logged the specific timeout/failure reason.
        return SignResult(signed=False, skip_reason=_SKIP_TIMEOUT)
    if completed.returncode != 0:
        log.warning(
            "cosign_sign_skipped",
            reason=_SKIP_FAILED,
            mode="key",
            returncode=completed.returncode,
            stderr=_safe_stderr(completed.stderr),
        )
        return SignResult(signed=False, skip_reason=_SKIP_FAILED)

    if not signature_path.exists():
        log.warning("cosign_sign_skipped", reason=_SKIP_FAILED, mode="key", detail="no signature")
        return SignResult(signed=False, skip_reason=_SKIP_FAILED)

    log.info("cosign_sign_succeeded", mode="key", signature=str(signature_path))
    return SignResult(signed=True, mode="key", signature_path=signature_path)


# ---------------------------------------------------------------------------
# Keyless (OIDC)
# ---------------------------------------------------------------------------


def _sign_keyless(
    *,
    blob_path: Path,
    signature_path: Path,
    certificate_path: Path,
    timeout: int,
) -> SignResult:
    """Keyless ``cosign sign-blob --yes`` (Fulcio/Rekor) flow — opt-in.

    cosign drives its own OIDC identity discovery (ambient CI token / configured
    provider) and writes both the detached signature AND the Fulcio-issued
    signing certificate. No private key / password is involved, so no Fernet
    decrypt happens on this path.
    """
    env = scrubbed_env_for_cosign()
    cmd = [
        "cosign",
        "sign-blob",
        "--yes",  # non-interactive (also consents to Rekor upload)
        # cosign v3 changed the defaults: sign-blob/attest-blob emit the new
        # bundle format and consult a signing config unless told otherwise.
        # Our verify path (and stored artifacts) expect the v2-style detached
        # signature/attestation files, so pin the old behaviour explicitly.
        "--new-bundle-format=false",
        "--use-signing-config=false",
        "--output-signature",
        str(signature_path),
        "--output-certificate",
        str(certificate_path),
        "--",
        str(blob_path),
    ]
    log.info("cosign_sign_start", mode="keyless", blob=str(blob_path))
    completed = _run_cosign(cmd, env=env, timeout=timeout)
    if completed is None:
        return SignResult(signed=False, skip_reason=_SKIP_TIMEOUT)
    if completed.returncode != 0:
        log.warning(
            "cosign_sign_skipped",
            reason=_SKIP_FAILED,
            mode="keyless",
            returncode=completed.returncode,
            stderr=_safe_stderr(completed.stderr),
        )
        return SignResult(signed=False, skip_reason=_SKIP_FAILED)

    if not signature_path.exists():
        log.warning(
            "cosign_sign_skipped", reason=_SKIP_FAILED, mode="keyless", detail="no signature"
        )
        return SignResult(signed=False, skip_reason=_SKIP_FAILED)

    # Keyless trust anchor: the Fulcio cert IS the verifiable identity (there is no
    # operator-held public key on this path). If cosign exited 0 but emitted no
    # certificate, a downstream consumer cannot verify the signature's identity — so
    # we must NOT report success. Skip (WARNING) rather than silently drop the trust
    # root. Mirrors :func:`_attest_keyless`. Key-based signing is unaffected.
    if not certificate_path.exists():
        log.warning(
            "cosign_sign_skipped",
            reason=_SKIP_NO_CERTIFICATE,
            mode="keyless",
            detail="cosign exited 0 but emitted no Fulcio certificate",
        )
        return SignResult(signed=False, skip_reason=_SKIP_NO_CERTIFICATE)

    log.info(
        "cosign_sign_succeeded",
        mode="keyless",
        signature=str(signature_path),
        certificate=str(certificate_path),
    )
    return SignResult(
        signed=True,
        mode="keyless",
        signature_path=signature_path,
        certificate_path=certificate_path,
    )


# ---------------------------------------------------------------------------
# Verification (optional helper — used by tests / future s2 download path)
# ---------------------------------------------------------------------------


def verify_blob(
    *,
    blob_path: Path,
    signature_path: Path,
    key_path: Path,
    backend: str | None = None,
    timeout_seconds: int | None = None,
) -> bool:
    """Verify a detached key-based signature over ``blob_path``.

    Returns ``True`` iff cosign reports a valid signature. NEVER raises — any
    error (missing binary, bad inputs, non-zero exit) returns ``False``. Keyless
    verification (Rekor / cert-identity flags) is deferred to s2.
    """
    mode_backend = (backend or scan_backend_mode()).lower()
    # Blob is a worker artifact → strict (no symlink). Signature may be operator-
    # supplied (a download path) → allow a symlink indirection.
    if not (_is_regular_blob(blob_path) and _is_regular_file(signature_path)):
        return False

    if mode_backend == "mock":
        return _verify_mock_signature(blob_path=blob_path, signature_path=signature_path)

    if shutil.which("cosign") is None:
        return False
    if not _is_regular_file(key_path):
        return False

    timeout = timeout_seconds if timeout_seconds is not None else cosign_timeout_seconds()
    cmd = [
        "cosign",
        "verify-blob",
        "--key",
        str(key_path),
        "--signature",
        str(signature_path),
        "--",
        str(blob_path),
    ]
    completed = _run_cosign(cmd, env=scrubbed_env_for_cosign(), timeout=timeout)
    return completed is not None and completed.returncode == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cosign(
    cmd: list[str],
    *,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[bytes] | None:
    """Spawn cosign with a fixed arg list. Returns ``None`` on timeout/spawn error.

    Never raises — a timeout or OSError (e.g. binary vanished between the
    ``shutil.which`` check and exec) is logged and reported as ``None`` so the
    caller degrades to "unsigned".
    """
    try:
        return subprocess.run(  # noqa: S603 — fixed args list, no shell
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        log.warning("cosign_sign_skipped", reason=_SKIP_TIMEOUT, timeout_seconds=timeout)
        return None
    except OSError as exc:
        log.warning("cosign_sign_skipped", reason=_SKIP_FAILED, detail=str(exc)[:200])
        return None


def _resolve_key_password() -> str:
    """Decrypt the stored cosign key password, or return ``""`` when unset.

    The plaintext is returned ONLY to the immediate caller, which places it in
    the subprocess env and never logs it. An empty string is correct for a
    passwordless key (cosign reads an empty ``COSIGN_PASSWORD``).

    Raises:
        SecretDecryptionError: when an encrypted password is set but cannot be
            decrypted (rotated key / corrupt ciphertext). The caller catches
            this and skips signing — the message carries no secret bytes.
    """
    token = cosign_key_password_encrypted()
    if token is None:
        return ""
    return decrypt_secret(token)


def _is_regular_file(path: Path) -> bool:
    """True iff ``path`` is an existing regular file (follows symlinks).

    Used for OPERATOR-configured paths (the cosign key, a verify-time signature)
    where a symlink — e.g. a mounted-secret indirection — is legitimate.
    """
    try:
        return path.is_file()
    except OSError:
        return False


def _is_regular_blob(path: Path) -> bool:
    """True iff ``path`` is an existing regular file that is NOT a symlink.

    Stricter than :func:`_is_regular_file`: the blob to sign is always a worker-
    generated workspace artifact (the CycloneDX SBOM written by the cdxgen
    stage), so it can never legitimately be a symlink. Rejecting symlinks here
    closes a symlink-swap window — a path that resolves to a regular file at
    check time but points elsewhere when cosign opens it — for the one input
    whose name could, in principle, be influenced upstream.
    """
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _safe_stderr(stderr: bytes | None) -> str:
    """Decode, secret-scrub, and cap cosign stderr for logging.

    cosign reads the key password from the ``COSIGN_PASSWORD`` *env var* and the
    keyless OIDC token from ``SIGSTORE_ID_TOKEN`` — neither is placed on argv, and
    cosign does not echo them, so its stderr is structurally secret-free. We still
    defensively scrub the free text before it hits the structured log: a future
    cosign version, a plugin, or a verbose error path could embed a token, and
    "trust the upstream not to leak" is not a control we want to depend on
    (CLAUDE.md §5 — PII/secret masking before logging).

    ``core.pii_mask.mask_pii`` redacts *structured* (dict-keyed) payloads and
    passes free-text strings through unchanged, so it cannot scrub a token
    embedded in a stderr line. We therefore pass the text through
    :func:`_scrub_secret_text` (token-shape redaction) first, then mask_pii (a
    no-op for the resulting str but kept so the documented masking pipeline is
    actually applied), then cap the length.
    """
    if not stderr:
        return ""
    text = stderr.decode("utf-8", errors="replace")
    scrubbed = _scrub_secret_text(text)
    # mask_pii is the documented masking helper; for a plain str it is a no-op,
    # but routing through it keeps a single, auditable masking entry point and
    # matches the module-docstring contract.
    masked = mask_pii(scrubbed)
    return str(masked)[:1000]


# Free-text token shapes to redact from cosign stderr before logging. These are
# the env-carried secrets on the cosign codepaths (COSIGN_PASSWORD /
# SIGSTORE_ID_TOKEN) plus generic bearer / JWT shapes, in case an error message
# ever interpolates one. Matched case-insensitively; the secret value is
# replaced with "***" while the surrounding diagnostic text is preserved.
_SECRET_TEXT_PATTERNS = (
    # `KEY=value` / `KEY: value` forms (env dump in a verbose error).
    re.compile(
        r"(?i)\b(COSIGN_PASSWORD|SIGSTORE_ID_TOKEN|COSIGN_EXPERIMENTAL_PASSWORD)\b"
        r"\s*[=:]\s*\S+"
    ),
    # `Authorization: Bearer <token>` headers.
    re.compile(r"(?i)\b(authorization|bearer)\b\s*[:=]?\s*[A-Za-z0-9._~+/-]{8,}={0,2}"),
    # Bare JWT (three base64url segments) — what an OIDC id-token looks like.
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"),
)


def _scrub_secret_text(text: str) -> str:
    """Redact secret-shaped substrings from free text, preserving readability.

    For each pattern, replaces the matched secret span with the key name (when
    present) plus ``***`` so a log reader still sees *which* secret was scrubbed
    without the value. Defensive: never raises (a regex over arbitrary bytes-
    decoded text can't, but we keep the contract explicit for callers in error
    paths).
    """

    def _sub(match: re.Match[str]) -> str:
        groups = [g for g in match.groups() if g]
        label = groups[0] if groups else "secret"
        return f"{label}=***"

    out = text
    for pattern in _SECRET_TEXT_PATTERNS:
        out = pattern.sub(_sub, out)
    return out


# ---------------------------------------------------------------------------
# Mock backend (unit tests / smoke harness)
# ---------------------------------------------------------------------------


def _write_mock_signature(
    *,
    blob_path: Path,
    signature_path: Path,
    certificate_path: Path,
    keyless: bool,
) -> SignResult:
    """Write deterministic fixture signature bytes without invoking cosign.

    The mock signature is a base64-looking sentinel derived from the blob's
    sha256 so a test can assert determinism. The keyless mock also writes a
    placeholder certificate. No key / password / network is touched.
    """
    import base64
    import hashlib

    digest = hashlib.sha256(blob_path.read_bytes()).digest()
    sig = base64.b64encode(digest).decode("ascii")
    signature_path.write_text(sig, encoding="utf-8")
    mode = "keyless" if keyless else "key"
    cert: Path | None = None
    if keyless:
        certificate_path.write_text(
            "-----BEGIN CERTIFICATE-----\nMOCK-COSIGN-CERT\n-----END CERTIFICATE-----\n",
            encoding="utf-8",
        )
        cert = certificate_path
    log.info("cosign_sign_mock", mode=mode, signature=str(signature_path))
    return SignResult(
        signed=True,
        mode=mode,
        signature_path=signature_path,
        certificate_path=cert,
    )


def _verify_mock_signature(*, blob_path: Path, signature_path: Path) -> bool:
    """Recompute the mock signature and compare — the mock-mode verify path."""
    import base64
    import hashlib

    expected = base64.b64encode(hashlib.sha256(blob_path.read_bytes()).digest()).decode("ascii")
    try:
        actual = signature_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return actual == expected


def _write_mock_attestation(
    *,
    predicate_path: Path,
    attestation_path: Path,
    certificate_path: Path,
    keyless: bool,
) -> AttestResult:
    """Write a deterministic fixture attestation without invoking cosign (v2.3-s2).

    The mock attestation wraps the (already-written) predicate in a minimal DSSE-
    shaped envelope so a test can assert the file exists + carries the predicate.
    No key / password / network is touched. The keyless mock also writes a
    placeholder certificate, mirroring :func:`_write_mock_signature`.
    """
    import base64

    predicate_b64 = base64.b64encode(predicate_path.read_bytes()).decode("ascii")
    envelope = {
        "payloadType": "application/vnd.in-toto+json",
        "payload": predicate_b64,
        "signatures": [{"sig": "MOCK-COSIGN-ATTESTATION"}],
    }
    attestation_path.write_text(json.dumps(envelope), encoding="utf-8")
    mode = "keyless" if keyless else "key"
    cert: Path | None = None
    if keyless:
        certificate_path.write_text(
            "-----BEGIN CERTIFICATE-----\nMOCK-COSIGN-CERT\n-----END CERTIFICATE-----\n",
            encoding="utf-8",
        )
        cert = certificate_path
    log.info("cosign_attest_mock", mode=mode, attestation=str(attestation_path))
    return AttestResult(
        attested=True,
        mode=mode,
        attestation_path=attestation_path,
        certificate_path=cert,
    )


__all__ = [
    "AttestResult",
    "SignResult",
    "attest_blob",
    "sign_blob",
    "verify_blob",
]
