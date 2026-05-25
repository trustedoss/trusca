"""
Unit tests for ``integrations.cosign.attest_blob`` — v2.3-s2 in-toto attestation.

Pure (no DB, no real cosign). Drives the adapter through the ``mock`` backend
(deterministic fixture attestation) and ``unittest.mock`` over ``subprocess.run``
for the real-mode argv / env / branch / failure-mode contract.

Coverage focus mirrors the s1 ``test_cosign.py`` signing suite:
  - key-based vs keyless argv (D2: key-based is the default), `attest-blob` +
    `--predicate` + `--type` + `--output-attestation`,
  - graceful skip on every failure mode (not installed / no key / key missing /
    decrypt fails / encryption misconfigured / predicate write fails / non-zero
    exit / timeout / no attestation file),
  - the password NEVER lands on argv (env-only),
  - worker secrets stripped from the subprocess env,
  - adversarial blob paths cannot inject a flag,
  - attest_blob NEVER raises into the scan (best-effort contract).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

SLSA_TYPE = "https://slsa.dev/provenance/v1"
_PREDICATE = {"buildDefinition": {"buildType": "x"}, "runDetails": {}}


def _write_blob(tmp_path: Path) -> Path:
    blob = tmp_path / "sbom.cdx.json"
    blob.write_bytes(b'{"bomFormat":"CycloneDX"}')
    return blob


def _write_key(tmp_path: Path) -> Path:
    key = tmp_path / "cosign.key"
    key.write_text("-----BEGIN ENCRYPTED COSIGN PRIVATE KEY-----\nx\n-----END...-----\n")
    return key


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = b""


@pytest.fixture
def cosign_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.cosign.shutil.which", lambda _name: "/usr/local/bin/cosign"
    )


@pytest.fixture
def real_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")


def _capture_run(monkeypatch: pytest.MonkeyPatch, *, write_outputs: bool = True) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["shell"] = kwargs.get("shell", False)
        if write_outputs:
            for i, tok in enumerate(cmd):
                if tok in ("--output-attestation", "--output-certificate"):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_text("attestation-bytes")
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr("integrations.cosign.subprocess.run", _fake_run)
    return captured


def _attest(tmp_path: Path, **overrides: Any) -> Any:
    from integrations import cosign

    kwargs: dict[str, Any] = {
        "blob_path": _write_blob(tmp_path),
        "predicate": _PREDICATE,
        "predicate_type": SLSA_TYPE,
        "output_dir": tmp_path / "out",
    }
    kwargs.update(overrides)
    return cosign.attest_blob(**kwargs)


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


def test_mock_key_based_writes_attestation(scan_backend_mock: None, tmp_path: Path) -> None:
    result = _attest(tmp_path, keyless=False)
    assert result.attested is True
    assert result.mode == "key"
    assert result.attestation_path is not None and result.attestation_path.exists()
    assert result.certificate_path is None
    # The mock envelope carries the predicate bytes (base64).
    envelope = json.loads(result.attestation_path.read_text())
    assert envelope["payloadType"] == "application/vnd.in-toto+json"
    assert envelope["payload"]


def test_mock_keyless_writes_attestation_and_certificate(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    result = _attest(tmp_path, keyless=True)
    assert result.attested is True
    assert result.mode == "keyless"
    assert result.attestation_path is not None and result.attestation_path.exists()
    assert result.certificate_path is not None and result.certificate_path.exists()


def test_keyless_toggle_read_from_env_when_arg_omitted(
    scan_backend_mock: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSIGN_KEYLESS", "true")
    assert _attest(tmp_path, output_dir=tmp_path / "a").mode == "keyless"
    monkeypatch.setenv("COSIGN_KEYLESS", "false")
    assert _attest(tmp_path, output_dir=tmp_path / "b").mode == "key"


# ---------------------------------------------------------------------------
# Graceful skips (best-effort contract)
# ---------------------------------------------------------------------------


def test_skip_when_blob_missing(scan_backend_mock: None, tmp_path: Path) -> None:
    from integrations import cosign

    result = cosign.attest_blob(
        blob_path=tmp_path / "nope.json",
        predicate=_PREDICATE,
        predicate_type=SLSA_TYPE,
        output_dir=tmp_path / "out",
    )
    assert result.attested is False
    assert result.skip_reason == "blob_path_not_a_regular_file"


def test_skip_when_blob_is_symlink(scan_backend_mock: None, tmp_path: Path) -> None:
    from integrations import cosign

    real = tmp_path / "real.json"
    real.write_bytes(b"{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    result = cosign.attest_blob(
        blob_path=link,
        predicate=_PREDICATE,
        predicate_type=SLSA_TYPE,
        output_dir=tmp_path / "out",
    )
    assert result.attested is False
    assert result.skip_reason == "blob_path_not_a_regular_file"


def test_skip_when_cosign_not_installed(
    real_backend: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("integrations.cosign.shutil.which", lambda _name: None)
    result = _attest(tmp_path)
    assert result.attested is False
    assert result.skip_reason == "cosign_not_installed"


def test_skip_key_based_when_no_key_configured(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)
    result = _attest(tmp_path, keyless=False)
    assert result.attested is False
    assert result.skip_reason == "key_based_no_key_configured"


def test_skip_key_based_when_key_file_missing(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSIGN_KEY_PATH", str(tmp_path / "absent.key"))
    result = _attest(tmp_path, keyless=False)
    assert result.attested is False
    assert result.skip_reason == "key_path_not_a_regular_file"


def test_skip_when_password_decrypt_fails(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("COSIGN_KEY_PATH", str(_write_key(tmp_path)))
    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", "not-a-real-fernet-token")
    result = _attest(tmp_path, keyless=False)
    assert result.attested is False
    assert result.skip_reason == "key_password_decrypt_failed"


def test_skip_when_key_encryption_misconfigured_prod_fail_closed(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSIGN_KEY_PATH", str(_write_key(tmp_path)))
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("GITHUB_APP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", "gAAAAA-some-token")
    result = _attest(tmp_path, keyless=False)
    assert result.attested is False
    assert result.skip_reason == "key_encryption_misconfigured"


def test_skip_when_predicate_write_fails(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A predicate-file write error is its own skip token (not a cosign exit)."""
    from integrations import cosign

    blob = _write_blob(tmp_path)

    real_write = Path.write_text

    def _boom(self: Path, *a: Any, **k: Any) -> int:
        if self.name == "sbom.predicate.json":
            raise OSError("disk full")
        return real_write(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _boom)
    result = cosign.attest_blob(
        blob_path=blob,
        predicate=_PREDICATE,
        predicate_type=SLSA_TYPE,
        output_dir=tmp_path / "out",
    )
    assert result.attested is False
    assert result.skip_reason == "predicate_write_failed"


def test_skip_on_cosign_nonzero_exit(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSIGN_KEY_PATH", str(_write_key(tmp_path)))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)
    monkeypatch.setattr(
        "integrations.cosign.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=1, stderr=b"attest error"),
    )
    result = _attest(tmp_path, keyless=False)
    assert result.attested is False
    assert result.skip_reason == "cosign_nonzero_exit"


def test_skip_on_cosign_timeout(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSIGN_KEY_PATH", str(_write_key(tmp_path)))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)

    def _raise_timeout(*_a: Any, **_k: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="cosign", timeout=1)

    monkeypatch.setattr("integrations.cosign.subprocess.run", _raise_timeout)
    result = _attest(tmp_path, keyless=False)
    assert result.attested is False
    assert result.skip_reason == "cosign_timeout"


def test_skip_when_zero_exit_but_no_attestation_file(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSIGN_KEY_PATH", str(_write_key(tmp_path)))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)
    monkeypatch.setattr(
        "integrations.cosign.subprocess.run", lambda *a, **k: _FakeCompleted(returncode=0)
    )
    result = _attest(tmp_path, keyless=False)
    assert result.attested is False
    assert result.skip_reason == "cosign_nonzero_exit"


def test_keyless_skip_on_nonzero_exit(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "integrations.cosign.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=1, stderr=b"no identity token found"),
    )
    result = _attest(tmp_path, keyless=True)
    assert result.attested is False
    assert result.skip_reason == "cosign_nonzero_exit"


def test_keyless_skip_on_timeout(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung keyless OIDC/Rekor round-trip times out → degrades to un-attested."""

    def _raise_timeout(*_a: Any, **_k: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="cosign", timeout=1)

    monkeypatch.setattr("integrations.cosign.subprocess.run", _raise_timeout)
    result = _attest(tmp_path, keyless=True)
    assert result.attested is False
    assert result.skip_reason == "cosign_timeout"


def test_keyless_skip_when_attestation_not_written(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "integrations.cosign.subprocess.run", lambda *a, **k: _FakeCompleted(returncode=0)
    )
    result = _attest(tmp_path, keyless=True)
    assert result.attested is False
    assert result.skip_reason == "cosign_nonzero_exit"


def test_keyless_skips_when_certificate_missing(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security hardening: keyless trust anchor is the Fulcio cert.

    If cosign exits 0 and writes the attestation but emits NO certificate, we
    must NOT report success — a consumer cannot establish identity without the
    cert. The result is a skip, not a degraded-but-"attested" envelope.
    """

    def _fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        # Write the attestation but deliberately NOT the certificate.
        for i, tok in enumerate(cmd):
            if tok == "--output-attestation":
                Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[i + 1]).write_text("att")
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr("integrations.cosign.subprocess.run", _fake_run)
    result = _attest(tmp_path, keyless=True)
    assert result.attested is False
    assert result.skip_reason == "keyless_certificate_missing"
    assert result.certificate_path is None


def test_keyless_succeeds_with_certificate(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy keyless path: cosign writes BOTH the attestation and the cert."""
    captured = _capture_run(monkeypatch)  # writes attestation + certificate
    result = _attest(tmp_path, keyless=True)
    assert result.attested is True
    assert result.mode == "keyless"
    assert result.certificate_path is not None and result.certificate_path.exists()
    assert "--output-certificate" in captured["cmd"]


def test_attest_never_raises_on_unexpected_oserror(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSIGN_KEY_PATH", str(_write_key(tmp_path)))
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)

    def _raise_oserror(*_a: Any, **_k: Any) -> None:
        raise OSError("exec format error")

    monkeypatch.setattr("integrations.cosign.subprocess.run", _raise_oserror)
    result = _attest(tmp_path, keyless=False)
    assert result.attested is False


# ---------------------------------------------------------------------------
# argv / env contract — security-critical
# ---------------------------------------------------------------------------


def test_key_based_argv_uses_attest_blob_and_predicate_and_type(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from core.crypto import encrypt_secret

    monkeypatch.setenv("COSIGN_KEY_PATH", str(_write_key(tmp_path)))
    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret_pw = "sup3r-s3cret-passphrase"
    monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", encrypt_secret(secret_pw))

    captured = _capture_run(monkeypatch)
    result = _attest(tmp_path, keyless=False)

    assert result.attested is True and result.mode == "key"
    cmd = captured["cmd"]
    assert cmd[0] == "cosign" and cmd[1] == "attest-blob"
    assert "--predicate" in cmd
    assert "--type" in cmd and SLSA_TYPE in cmd
    assert "--output-attestation" in cmd
    assert "--key" in cmd
    assert "--" in cmd  # blob after sentinel
    # The PASSWORD never appears on argv.
    assert all(secret_pw not in tok for tok in cmd)
    # It is in the subprocess ENV (decrypted) instead.
    assert captured["env"]["COSIGN_PASSWORD"] == secret_pw
    assert captured["shell"] is False


def test_keyless_argv_requests_attestation_and_certificate_no_key(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_run(monkeypatch)
    result = _attest(tmp_path, keyless=True)

    assert result.attested is True and result.mode == "keyless"
    cmd = captured["cmd"]
    assert "--key" not in cmd
    assert "--yes" in cmd
    assert "--output-attestation" in cmd
    assert "--output-certificate" in cmd
    assert "COSIGN_PASSWORD" not in captured["env"]


def test_predicate_written_to_disk_is_what_was_passed(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The --predicate file holds exactly the predicate dict we passed in."""
    captured = _capture_run(monkeypatch)
    _attest(tmp_path, keyless=True, predicate={"k": "v", "buildDefinition": {}})

    cmd = captured["cmd"]
    pred_path = Path(cmd[cmd.index("--predicate") + 1])
    assert json.loads(pred_path.read_text()) == {"k": "v", "buildDefinition": {}}


def test_attest_env_strips_worker_secrets(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DT_API_KEY", "dt-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("SECRET_KEY", "j" * 40)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")

    captured = _capture_run(monkeypatch)
    _attest(tmp_path, keyless=True)

    env = captured["env"]
    assert "DT_API_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "SECRET_KEY" not in env
    assert "SLACK_WEBHOOK_URL" not in env


@pytest.mark.parametrize(
    "hostile_name",
    [
        "--output-attestation",
        "-rf",
        "; rm -rf /",
        "$(touch pwned)",
        "a\nb",
    ],
)
def test_adversarial_blob_path_skips_pre_spawn(
    scan_backend_mock: None, tmp_path: Path, hostile_name: str
) -> None:
    from integrations import cosign

    result = cosign.attest_blob(
        blob_path=tmp_path / hostile_name,
        predicate=_PREDICATE,
        predicate_type=SLSA_TYPE,
        output_dir=tmp_path / "out",
    )
    assert result.attested is False
    assert result.skip_reason == "blob_path_not_a_regular_file"


def test_real_file_named_like_a_flag_passed_after_double_dash(
    real_backend: None, cosign_installed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import cosign

    weird = tmp_path / "--output-attestation"
    weird.write_bytes(b"{}")
    captured = _capture_run(monkeypatch)
    monkeypatch.setenv("COSIGN_KEYLESS", "true")
    result = cosign.attest_blob(
        blob_path=weird,
        predicate=_PREDICATE,
        predicate_type=SLSA_TYPE,
        output_dir=tmp_path / "out",
    )
    assert result.attested is True
    cmd = captured["cmd"]
    dd_index = cmd.index("--")
    assert str(weird) in cmd[dd_index + 1 :]
