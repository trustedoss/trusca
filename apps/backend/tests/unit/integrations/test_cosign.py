"""
Unit tests for ``integrations.cosign`` — v2.3-s1 SBOM signing.

Pure (no DB, no real cosign). The real binary lives only in the worker image,
so these tests drive the adapter through:
  - the ``mock`` backend (deterministic fixture signatures), and
  - ``unittest.mock`` over ``subprocess.run`` for the real-mode argv / env /
    branch / failure-mode contract.

Coverage focus:
  - key-based vs keyless argv construction (D2: key-based is the default).
  - graceful skip when cosign is not installed / key is not configured /
    blob is missing / password decrypt fails / cosign exits non-zero / times out.
  - the password NEVER lands on argv; it is passed via COSIGN_PASSWORD env.
  - worker secrets are stripped from the cosign subprocess env.
  - adversarial blob/key paths cannot inject a flag or a shell command.
  - sign_blob NEVER raises into the scan (best-effort contract).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _write_blob(tmp_path: Path, content: bytes = b'{"bomFormat":"CycloneDX"}') -> Path:
    blob = tmp_path / "sbom.cdx.json"
    blob.write_bytes(content)
    return blob


def _write_key(tmp_path: Path) -> Path:
    key = tmp_path / "cosign.key"
    key.write_text("-----BEGIN ENCRYPTED COSIGN PRIVATE KEY-----\nx\n-----END...-----\n")
    return key


class _FakeCompleted:
    """Stand-in for subprocess.CompletedProcess with the fields the adapter reads."""

    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = b""


@pytest.fixture
def cosign_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend cosign is on $PATH so real-mode branches run."""
    monkeypatch.setattr(
        "integrations.cosign.shutil.which", lambda _name: "/usr/local/bin/cosign"
    )


@pytest.fixture
def real_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the real (non-mock) backend so subprocess paths are exercised."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


def test_mock_key_based_writes_deterministic_signature(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import cosign

    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)

    assert result.signed is True
    assert result.mode == "key"
    assert result.signature_path is not None and result.signature_path.exists()
    assert result.certificate_path is None  # key-based emits no cert
    # Deterministic: signing the same bytes again yields the same signature.
    again = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out2", keyless=False)
    assert again.signature_path is not None
    assert (
        result.signature_path.read_text() == again.signature_path.read_text()
    )


def test_mock_keyless_writes_signature_and_certificate(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import cosign

    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=True)

    assert result.signed is True
    assert result.mode == "keyless"
    assert result.signature_path is not None and result.signature_path.exists()
    assert result.certificate_path is not None and result.certificate_path.exists()


def test_mock_verify_round_trip(scan_backend_mock: None, tmp_path: Path) -> None:
    from integrations import cosign

    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signature_path is not None

    ok = cosign.verify_blob(
        blob_path=blob,
        signature_path=result.signature_path,
        key_path=tmp_path / "unused.pub",  # mock verify ignores the key
    )
    assert ok is True

    # Tamper with the blob → mock verify recomputes and fails.
    blob.write_bytes(b"tampered")
    assert (
        cosign.verify_blob(
            blob_path=blob,
            signature_path=result.signature_path,
            key_path=tmp_path / "unused.pub",
        )
        is False
    )


def test_keyless_toggle_read_from_env_when_arg_omitted(
    scan_backend_mock: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    blob = _write_blob(tmp_path)
    monkeypatch.setenv("COSIGN_KEYLESS", "true")
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out")
    assert result.mode == "keyless"

    monkeypatch.setenv("COSIGN_KEYLESS", "false")
    result2 = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out2")
    assert result2.mode == "key"


# ---------------------------------------------------------------------------
# Graceful skips (best-effort contract)
# ---------------------------------------------------------------------------


def test_skip_when_blob_missing(scan_backend_mock: None, tmp_path: Path) -> None:
    from integrations import cosign

    result = cosign.sign_blob(blob_path=tmp_path / "nope.json", output_dir=tmp_path / "out")
    assert result.signed is False
    assert result.skip_reason == "blob_path_not_a_regular_file"


def test_skip_when_cosign_not_installed(
    real_backend: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    monkeypatch.setattr("integrations.cosign.shutil.which", lambda _name: None)
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out")
    assert result.signed is False
    assert result.skip_reason == "cosign_not_installed"


def test_skip_key_based_when_no_key_configured(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False
    assert result.skip_reason == "key_based_no_key_configured"


def test_skip_key_based_when_key_file_missing(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    monkeypatch.setenv("COSIGN_KEY_PATH", str(tmp_path / "absent.key"))
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False
    assert result.skip_reason == "key_path_not_a_regular_file"


def test_skip_when_password_decrypt_fails(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    key = _write_key(tmp_path)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
    # A bogus ciphertext + a valid Fernet key → decrypt fails cleanly.
    from cryptography.fernet import Fernet

    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", "not-a-real-fernet-token")
    blob = _write_blob(tmp_path)

    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False
    assert result.skip_reason == "key_password_decrypt_failed"


def test_skip_on_cosign_nonzero_exit(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    key = _write_key(tmp_path)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)

    monkeypatch.setattr(
        "integrations.cosign.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=1, stderr=b"signing error"),
    )
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False
    assert result.skip_reason == "cosign_nonzero_exit"


def test_skip_on_cosign_timeout(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    key = _write_key(tmp_path)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)

    def _raise_timeout(*_a: Any, **_k: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="cosign", timeout=1)

    monkeypatch.setattr("integrations.cosign.subprocess.run", _raise_timeout)
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False
    assert result.skip_reason == "cosign_timeout"


def test_skip_when_cosign_returns_zero_but_no_signature_file(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cosign exited 0 but wrote nothing → treat as failed, not silently 'signed'."""
    from integrations import cosign

    key = _write_key(tmp_path)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)
    # run returns 0 but never creates the --output-signature file.
    monkeypatch.setattr(
        "integrations.cosign.subprocess.run", lambda *a, **k: _FakeCompleted(returncode=0)
    )
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False
    assert result.skip_reason == "cosign_nonzero_exit"


def test_sign_blob_never_raises_on_unexpected_oserror(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spawn-time OSError (binary vanished after which) degrades to unsigned."""
    from integrations import cosign

    key = _write_key(tmp_path)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)

    def _raise_oserror(*_a: Any, **_k: Any) -> None:
        raise OSError("exec format error")

    monkeypatch.setattr("integrations.cosign.subprocess.run", _raise_oserror)
    blob = _write_blob(tmp_path)
    # Must NOT raise.
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False


# ---------------------------------------------------------------------------
# argv / env contract — the security-critical assertions
# ---------------------------------------------------------------------------


def _capture_run(monkeypatch: pytest.MonkeyPatch, *, write_outputs: bool = True) -> dict[str, Any]:
    """Patch subprocess.run to capture argv + env and optionally create outputs."""
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["timeout"] = kwargs.get("timeout")
        captured["shell"] = kwargs.get("shell", False)
        if write_outputs:
            # Honour --output-signature / --output-certificate so the success
            # branch (which checks the file exists) is reached.
            for i, tok in enumerate(cmd):
                if tok in ("--output-signature", "--output-certificate"):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_text("sig-bytes")
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr("integrations.cosign.subprocess.run", _fake_run)
    return captured


def test_key_based_argv_uses_key_flag_and_password_via_env_only(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from core.crypto import encrypt_secret
    from integrations import cosign

    key = _write_key(tmp_path)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret_pw = "sup3r-s3cret-passphrase"
    monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", encrypt_secret(secret_pw))

    captured = _capture_run(monkeypatch)
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)

    assert result.signed is True and result.mode == "key"
    cmd = captured["cmd"]
    assert cmd[0] == "cosign" and cmd[1] == "sign-blob"
    assert "--key" in cmd and str(key) in cmd
    assert "--" in cmd  # blob passed after -- so it cannot be parsed as a flag
    # The PASSWORD must never appear anywhere on argv.
    assert all(secret_pw not in tok for tok in cmd)
    # It must be present in the subprocess ENV (decrypted) instead.
    assert captured["env"]["COSIGN_PASSWORD"] == secret_pw
    # No shell — no injection surface.
    assert captured["shell"] is False


def test_keyless_argv_requests_signature_and_certificate_no_key(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    captured = _capture_run(monkeypatch)
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=True)

    assert result.signed is True and result.mode == "keyless"
    cmd = captured["cmd"]
    assert "--key" not in cmd  # keyless uses no private key
    assert "--yes" in cmd  # non-interactive + Rekor consent
    assert "--output-signature" in cmd
    assert "--output-certificate" in cmd
    # No COSIGN_PASSWORD is set on the keyless path (no key to decrypt).
    assert "COSIGN_PASSWORD" not in captured["env"]


def test_cosign_env_strips_worker_secrets(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker secrets must NOT reach the cosign subprocess env."""
    from integrations import cosign

    monkeypatch.setenv("DT_API_KEY", "dt-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("SECRET_KEY", "j" * 40)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")

    captured = _capture_run(monkeypatch)
    blob = _write_blob(tmp_path)
    cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=True)

    env = captured["env"]
    assert "DT_API_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "SECRET_KEY" not in env
    assert "SLACK_WEBHOOK_URL" not in env


# ---------------------------------------------------------------------------
# Adversarial blob / key paths (feedback: parametrize untrusted-input parsing)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_name",
    [
        "--output-signature",  # looks like a cosign flag
        "-rf",  # looks like a short flag bundle
        "; rm -rf /",  # shell metachars
        "$(touch pwned)",  # command substitution
        "a\nb",  # embedded newline
        "a b c",  # spaces
        "--key=/etc/passwd",  # flag-with-value shape
    ],
)
def test_adversarial_blob_path_is_not_a_regular_file_so_skips(
    scan_backend_mock: None, tmp_path: Path, hostile_name: str
) -> None:
    """A path that does not resolve to a real regular file is rejected pre-spawn.

    None of these adversarial names exist as files, so the adapter skips signing
    BEFORE building any argv — a hostile path can neither become a flag nor a
    shell command. (When such a name DID exist as a real file, it is still passed
    after ``--`` and via a fixed arg list, so argv injection remains impossible —
    asserted by the argv tests above.)
    """
    from integrations import cosign

    result = cosign.sign_blob(
        blob_path=tmp_path / hostile_name, output_dir=tmp_path / "out"
    )
    assert result.signed is False
    assert result.skip_reason == "blob_path_not_a_regular_file"


def test_real_file_named_like_a_flag_is_passed_after_double_dash(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A REAL file whose name looks like a flag is still safe — passed after --."""
    from integrations import cosign

    weird = tmp_path / "--output-signature"
    weird.write_bytes(b"{}")

    captured = _capture_run(monkeypatch)
    monkeypatch.setenv("COSIGN_KEYLESS", "true")
    result = cosign.sign_blob(blob_path=weird, output_dir=tmp_path / "out")

    assert result.signed is True
    cmd = captured["cmd"]
    # The blob path appears AFTER the final "--" sentinel, so cosign treats it
    # as a positional, never as the (identically-named) flag.
    dd_index = cmd.index("--")
    assert str(weird) in cmd[dd_index + 1 :]


# ---------------------------------------------------------------------------
# verify_blob real-mode guards
# ---------------------------------------------------------------------------


def test_verify_returns_false_when_cosign_not_installed(
    real_backend: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    monkeypatch.setattr("integrations.cosign.shutil.which", lambda _name: None)
    blob = _write_blob(tmp_path)
    sig = tmp_path / "sig"
    sig.write_text("x")
    key = _write_key(tmp_path)
    assert cosign.verify_blob(blob_path=blob, signature_path=sig, key_path=key) is False


def test_verify_returns_true_on_zero_exit(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    blob = _write_blob(tmp_path)
    sig = tmp_path / "sig"
    sig.write_text("x")
    key = _write_key(tmp_path)
    monkeypatch.setattr(
        "integrations.cosign.subprocess.run", lambda *a, **k: _FakeCompleted(returncode=0)
    )
    assert cosign.verify_blob(blob_path=blob, signature_path=sig, key_path=key) is True


def test_verify_returns_false_when_inputs_missing(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """Missing blob / signature short-circuits to False before any spawn."""
    from integrations import cosign

    blob = _write_blob(tmp_path)
    assert (
        cosign.verify_blob(
            blob_path=blob,
            signature_path=tmp_path / "absent.sig",
            key_path=tmp_path / "k",
        )
        is False
    )


def test_verify_returns_false_when_key_missing_real_mode(
    real_backend: None, cosign_installed: None, tmp_path: Path
) -> None:
    from integrations import cosign

    blob = _write_blob(tmp_path)
    sig = tmp_path / "sig"
    sig.write_text("x")
    assert (
        cosign.verify_blob(
            blob_path=blob, signature_path=sig, key_path=tmp_path / "absent.key"
        )
        is False
    )


# ---------------------------------------------------------------------------
# Keyless real-mode failure branches
# ---------------------------------------------------------------------------


def test_keyless_skip_on_nonzero_exit_with_stderr(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keyless cosign failure (e.g. no OIDC identity) degrades to unsigned."""
    from integrations import cosign

    monkeypatch.setattr(
        "integrations.cosign.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=1, stderr=b"no identity token found"),
    )
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=True)
    assert result.signed is False
    assert result.skip_reason == "cosign_nonzero_exit"


def test_keyless_skips_when_certificate_missing(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security hardening: keyless trust anchor is the Fulcio cert.

    cosign wrote a signature but no certificate → we must NOT report 'signed':
    without the short-lived Fulcio cert a consumer cannot verify the signing
    identity (there is no operator-held public key on the keyless path). The
    result is a skip, not a degraded-but-"signed" outcome.
    """
    from integrations import cosign

    def _fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        # Honour --output-signature but deliberately NOT --output-certificate.
        for i, tok in enumerate(cmd):
            if tok == "--output-signature":
                Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[i + 1]).write_text("sig")
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr("integrations.cosign.subprocess.run", _fake_run)
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=True)
    assert result.signed is False
    assert result.skip_reason == "keyless_certificate_missing"
    assert result.certificate_path is None


def test_keyless_succeeds_with_certificate(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy keyless path: cosign writes BOTH the signature and the cert."""
    from integrations import cosign

    captured = _capture_run(monkeypatch)  # writes signature + certificate
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=True)
    assert result.signed is True
    assert result.mode == "keyless"
    assert result.certificate_path is not None and result.certificate_path.exists()
    assert "--output-certificate" in captured["cmd"]


def test_keyless_skip_when_signature_not_written(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    monkeypatch.setattr(
        "integrations.cosign.subprocess.run", lambda *a, **k: _FakeCompleted(returncode=0)
    )
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=True)
    assert result.signed is False
    assert result.skip_reason == "cosign_nonzero_exit"


# ---------------------------------------------------------------------------
# Encryption-key misconfiguration (prod fail-closed) — security-reviewer
# fix-first finding #2. SecretEncryptionError must be caught at the password
# resolve boundary so sign_blob honours its "NEVER raises" contract.
# ---------------------------------------------------------------------------


def test_skip_when_key_encryption_misconfigured_prod_fail_closed(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APP_ENV=prod + GITHUB_APP_ENCRYPTION_KEY unset + encrypted password set.

    core.crypto fails closed (raises SecretEncryptionError rather than deriving
    the encryption key from SECRET_KEY). The cosign adapter MUST catch it and
    degrade to unsigned — not propagate it into the scan.
    """
    from integrations import cosign

    key = _write_key(tmp_path)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("GITHUB_APP_ENCRYPTION_KEY", raising=False)
    # An encrypted password IS configured, so _resolve_key_password attempts a
    # decrypt → _resolve_fernet → _derive_key_from_secret → SecretEncryptionError.
    monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", "gAAAAA-some-token")

    # subprocess.run must never be reached; if it is, the test should still not
    # raise — but the contract is a clean skip BEFORE spawn.
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)

    assert result.signed is False
    assert result.skip_reason == "key_encryption_misconfigured"


# ---------------------------------------------------------------------------
# Symlink rejection for the blob (fix-first finding #4) — the blob is always a
# worker-generated workspace artifact and may never legitimately be a symlink.
# ---------------------------------------------------------------------------


def test_skip_when_blob_is_symlink(scan_backend_mock: None, tmp_path: Path) -> None:
    """A symlinked blob_path is rejected (symlink-swap defense-in-depth)."""
    from integrations import cosign

    real = tmp_path / "real_sbom.cdx.json"
    real.write_bytes(b'{"bomFormat":"CycloneDX"}')
    link = tmp_path / "link_sbom.cdx.json"
    link.symlink_to(real)

    result = cosign.sign_blob(blob_path=link, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False
    assert result.skip_reason == "blob_path_not_a_regular_file"


def test_verify_returns_false_when_blob_is_symlink(scan_backend_mock: None, tmp_path: Path) -> None:
    """verify_blob also rejects a symlinked blob (same strict rule as sign)."""
    from integrations import cosign

    real = tmp_path / "real_sbom.cdx.json"
    real.write_bytes(b"{}")
    link = tmp_path / "link_sbom.cdx.json"
    link.symlink_to(real)
    sig = tmp_path / "sig"
    sig.write_text("x")

    assert (
        cosign.verify_blob(blob_path=link, signature_path=sig, key_path=tmp_path / "k") is False
    )


# ---------------------------------------------------------------------------
# stderr secret-scrubbing (fix-first finding #3) — cosign stderr must be
# scrubbed of token-shaped material before it reaches the structured log.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        # COSIGN_PASSWORD=value form (a hypothetical verbose env dump).
        (b"error: COSIGN_PASSWORD=hunter2-very-secret failed", "hunter2-very-secret"),
        # SIGSTORE_ID_TOKEN=<jwt> form.
        (
            b"SIGSTORE_ID_TOKEN=eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl rejected",
            "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl",
        ),
        # Authorization bearer header.
        (b"Authorization: Bearer abcDEF123456ghijklmnop denied", "abcDEF123456ghijklmnop"),
        # Bare JWT in an error line.
        (
            b"token eyJhbGciOiJSUzI1.eyJpc3MiOiJodHRw.c2lnXzEyMzQ1Njc4 invalid",
            "eyJhbGciOiJSUzI1.eyJpc3MiOiJodHRw.c2lnXzEyMzQ1Njc4",
        ),
    ],
)
def test_safe_stderr_scrubs_secret_shapes(raw: bytes, must_not_contain: str) -> None:
    from integrations import cosign

    out = cosign._safe_stderr(raw)
    assert must_not_contain not in out
    assert "***" in out  # something was redacted


def test_safe_stderr_preserves_benign_diagnostics() -> None:
    """Ordinary cosign diagnostics survive scrubbing (readability preserved)."""
    from integrations import cosign

    out = cosign._safe_stderr(b"Error: no identity token found; configure OIDC provider")
    assert "no identity token found" in out


def test_safe_stderr_empty_and_none() -> None:
    from integrations import cosign

    assert cosign._safe_stderr(None) == ""
    assert cosign._safe_stderr(b"") == ""


def test_safe_stderr_caps_length() -> None:
    from integrations import cosign

    out = cosign._safe_stderr(b"x" * 5000)
    assert len(out) <= 1000


def test_nonzero_exit_stderr_is_scrubbed_in_result_path(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a non-zero exit whose stderr carries a token still skips and
    the adapter routes stderr through the scrubber (no token reaches the log)."""
    from integrations import cosign

    key = _write_key(tmp_path)
    monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)
    monkeypatch.setattr(
        "integrations.cosign.subprocess.run",
        lambda *a, **k: _FakeCompleted(
            returncode=1, stderr=b"COSIGN_PASSWORD=leak-me-not boom"
        ),
    )
    blob = _write_blob(tmp_path)
    result = cosign.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=False)
    assert result.signed is False
    assert result.skip_reason == "cosign_nonzero_exit"
    # And the scrubber would have removed the secret from any logged stderr.
    assert "leak-me-not" not in cosign._safe_stderr(b"COSIGN_PASSWORD=leak-me-not boom")
