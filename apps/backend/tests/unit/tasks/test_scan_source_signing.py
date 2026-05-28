"""
Unit tests for the scan_source cosign-signing wiring (v2.3-s1).

Covers ``tasks.scan_source._sign_sbom`` in isolation — the cosign adapter and the
``_persist_artifact`` DB helper are both mocked, so no real cosign / Postgres is
needed. We pin:

  - success (key-based) persists exactly the ``sbom_cyclonedx_sig`` artifact with
    the SBOM's sha256 in ``sha256`` (the previously-unused column),
  - success (keyless) additionally persists ``sbom_cyclonedx_cert``,
  - a best-effort SKIP (signed=False) persists NOTHING and never raises,
  - an UNEXPECTED error (e.g. a DB failure persisting the artifact) is swallowed
    so signing can never break a scan,
  - the artifact ``kind`` strings fit the ScanArtifact.kind String(32) column.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from integrations.cosign import SignResult
from tasks import scan_source as mod


def _make_sbom(tmp_path: Path) -> Path:
    sbom = tmp_path / "cdxgen" / "cdxgen.cdx.json"
    sbom.parent.mkdir(parents=True, exist_ok=True)
    sbom.write_bytes(b'{"bomFormat":"CycloneDX","specVersion":"1.5"}')
    return sbom


def _patch_persist(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture _persist_artifact calls instead of hitting the DB."""
    calls: list[dict[str, Any]] = []

    def _fake_persist(
        scan_uuid: uuid.UUID, *, kind: str, path: Path, sha256: str | None = None
    ) -> None:
        calls.append({"scan_uuid": scan_uuid, "kind": kind, "path": path, "sha256": sha256})

    monkeypatch.setattr(mod, "_persist_artifact", _fake_persist)
    return calls


def test_sign_key_based_persists_signature_with_sbom_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    sbom = _make_sbom(tmp_path)
    sig = tmp_path / "cosign" / "sbom.cdx.json.sig"
    sig.parent.mkdir(parents=True, exist_ok=True)
    sig.write_text("signature")

    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.sign_blob",
        lambda **_k: SignResult(signed=True, mode="key", signature_path=sig),
    )
    calls = _patch_persist(monkeypatch)

    scan_id = uuid.uuid4()
    mod._sign_sbom(scan_uuid=scan_id, sbom_path=sbom, workspace=tmp_path)

    assert len(calls) == 1
    (call,) = calls
    assert call["kind"] == "sbom_cyclonedx_sig"
    assert call["path"] == sig
    assert call["sha256"] == hashlib.sha256(sbom.read_bytes()).hexdigest()


def test_sign_keyless_persists_signature_and_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)
    sig = tmp_path / "cosign" / "sbom.cdx.json.sig"
    cert = tmp_path / "cosign" / "sbom.cdx.json.cert"
    sig.parent.mkdir(parents=True, exist_ok=True)
    sig.write_text("signature")
    cert.write_text("cert")

    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.sign_blob",
        lambda **_k: SignResult(
            signed=True, mode="keyless", signature_path=sig, certificate_path=cert
        ),
    )
    calls = _patch_persist(monkeypatch)

    mod._sign_sbom(scan_uuid=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path)

    kinds = {c["kind"] for c in calls}
    assert kinds == {"sbom_cyclonedx_sig", "sbom_cyclonedx_cert"}


def test_sign_skip_persists_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sbom = _make_sbom(tmp_path)
    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.sign_blob",
        lambda **_k: SignResult(signed=False, skip_reason="cosign_not_installed"),
    )
    calls = _patch_persist(monkeypatch)

    # Must not raise.
    mod._sign_sbom(scan_uuid=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path)
    assert calls == []


def test_sign_swallows_unexpected_adapter_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)

    def _boom(**_k: Any) -> SignResult:
        raise RuntimeError("totally unexpected")

    monkeypatch.setattr("tasks.scan_source.cosign_adapter.sign_blob", _boom)
    calls = _patch_persist(monkeypatch)

    # An unexpected error in the adapter MUST NOT propagate (signing is auxiliary).
    mod._sign_sbom(scan_uuid=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path)
    assert calls == []


def test_sign_swallows_persist_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DB failure persisting the signature must not break the scan."""
    sbom = _make_sbom(tmp_path)
    sig = tmp_path / "cosign" / "sbom.cdx.json.sig"
    sig.parent.mkdir(parents=True, exist_ok=True)
    sig.write_text("signature")

    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.sign_blob",
        lambda **_k: SignResult(signed=True, mode="key", signature_path=sig),
    )

    def _persist_boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "_persist_artifact", _persist_boom)

    # Must not raise.
    mod._sign_sbom(scan_uuid=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path)


def test_signing_artifact_kinds_fit_column_width() -> None:
    """ScanArtifact.kind is String(32) — the signing kinds must fit."""
    assert len(mod._SBOM_SIG_KIND) <= 32
    assert len(mod._SBOM_CERT_KIND) <= 32


def test_sign_stage_registered_with_monotonic_percent() -> None:
    """The 'sign' stage sits between cdxgen and scancode for monotonic WS frames."""
    assert mod._STAGE_PROGRESS["cdxgen"] < mod._STAGE_PROGRESS["sign"]
    assert mod._STAGE_PROGRESS["sign"] < mod._STAGE_PROGRESS["scancode"]
