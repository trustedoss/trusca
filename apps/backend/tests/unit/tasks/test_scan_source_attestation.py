"""
Unit tests for the scan_source attestation wiring (v2.3-s2).

Covers ``tasks.scan_source._attest_sbom`` (and the s1 ``_sign_sbom`` return
contract that gates it) in isolation — the cosign adapter, the predicate
builder, and ``_persist_artifact`` are mocked, so no real cosign / Postgres is
needed. We pin:

  - success (key-based) persists exactly the ``sbom_attestation`` artifact with
    the SBOM's sha256 in ``sha256``,
  - success (keyless) additionally persists ``sbom_attest_cert``,
  - a best-effort SKIP (attested=False) persists NOTHING and never raises,
  - an UNEXPECTED error (adapter / persist) is swallowed,
  - the predicate handed to cosign carries the scan/project ids + build context
    and NO secrets,
  - the artifact ``kind`` strings fit ScanArtifact.kind String(32),
  - ``_sign_sbom`` returns True/False so the caller can gate attestation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from integrations.cosign import AttestResult, SignResult
from tasks import scan_source as mod


def _make_sbom(tmp_path: Path) -> Path:
    sbom = tmp_path / "cdxgen" / "cdxgen.cdx.json"
    sbom.parent.mkdir(parents=True, exist_ok=True)
    sbom.write_bytes(b'{"bomFormat":"CycloneDX","specVersion":"1.5"}')
    return sbom


def _patch_persist(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_persist(
        scan_uuid: uuid.UUID, *, kind: str, path: Path, sha256: str | None = None
    ) -> None:
        calls.append({"scan_uuid": scan_uuid, "kind": kind, "path": path, "sha256": sha256})

    monkeypatch.setattr(mod, "_persist_artifact", _fake_persist)
    return calls


# ---------------------------------------------------------------------------
# _attest_sbom — success
# ---------------------------------------------------------------------------


def test_attest_key_based_persists_attestation_with_sbom_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)
    att = tmp_path / "cosign" / "sbom.intoto.jsonl"
    att.parent.mkdir(parents=True, exist_ok=True)
    att.write_text("attestation")

    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.attest_blob",
        lambda **_k: AttestResult(attested=True, mode="key", attestation_path=att),
    )
    calls = _patch_persist(monkeypatch)

    scan_id = uuid.uuid4()
    mod._attest_sbom(
        scan_uuid=scan_id, project_id=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path
    )

    assert len(calls) == 1
    (call,) = calls
    assert call["kind"] == "sbom_attestation"
    assert call["path"] == att
    assert call["sha256"] == hashlib.sha256(sbom.read_bytes()).hexdigest()


def test_attest_keyless_persists_attestation_and_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)
    att = tmp_path / "cosign" / "sbom.intoto.jsonl"
    cert = tmp_path / "cosign" / "sbom.attest.cert"
    att.parent.mkdir(parents=True, exist_ok=True)
    att.write_text("attestation")
    cert.write_text("cert")

    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.attest_blob",
        lambda **_k: AttestResult(
            attested=True, mode="keyless", attestation_path=att, certificate_path=cert
        ),
    )
    calls = _patch_persist(monkeypatch)

    mod._attest_sbom(
        scan_uuid=uuid.uuid4(), project_id=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path
    )

    kinds = {c["kind"] for c in calls}
    assert kinds == {"sbom_attestation", "sbom_attest_cert"}


# ---------------------------------------------------------------------------
# _attest_sbom — best-effort skips / swallows
# ---------------------------------------------------------------------------


def test_attest_skip_persists_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sbom = _make_sbom(tmp_path)
    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.attest_blob",
        lambda **_k: AttestResult(attested=False, skip_reason="cosign_not_installed"),
    )
    calls = _patch_persist(monkeypatch)

    mod._attest_sbom(
        scan_uuid=uuid.uuid4(), project_id=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path
    )
    assert calls == []


def test_attest_swallows_unexpected_adapter_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)

    def _boom(**_k: Any) -> AttestResult:
        raise RuntimeError("totally unexpected")

    monkeypatch.setattr("tasks.scan_source.cosign_adapter.attest_blob", _boom)
    calls = _patch_persist(monkeypatch)

    mod._attest_sbom(
        scan_uuid=uuid.uuid4(), project_id=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path
    )
    assert calls == []


def test_attest_swallows_persist_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)
    att = tmp_path / "cosign" / "sbom.intoto.jsonl"
    att.parent.mkdir(parents=True, exist_ok=True)
    att.write_text("attestation")

    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.attest_blob",
        lambda **_k: AttestResult(attested=True, mode="key", attestation_path=att),
    )

    def _persist_boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "_persist_artifact", _persist_boom)

    mod._attest_sbom(
        scan_uuid=uuid.uuid4(), project_id=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path
    )


# ---------------------------------------------------------------------------
# Predicate content handed to cosign
# ---------------------------------------------------------------------------


def test_predicate_passed_to_cosign_carries_ids_and_no_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)
    att = tmp_path / "cosign" / "sbom.intoto.jsonl"
    att.parent.mkdir(parents=True, exist_ok=True)
    att.write_text("attestation")

    captured: dict[str, Any] = {}

    def _fake_attest(**kwargs: Any) -> AttestResult:
        captured.update(kwargs)
        return AttestResult(attested=True, mode="key", attestation_path=att)

    monkeypatch.setattr("tasks.scan_source.cosign_adapter.attest_blob", _fake_attest)
    _patch_persist(monkeypatch)

    scan_id = uuid.uuid4()
    project_id = uuid.uuid4()
    monkeypatch.setenv("DT_API_KEY", "dt-secret-should-not-appear")
    mod._attest_sbom(
        scan_uuid=scan_id, project_id=project_id, sbom_path=sbom, workspace=tmp_path
    )

    assert captured["predicate_type"] == "https://slsa.dev/provenance/v1"
    predicate = captured["predicate"]
    bd = predicate["buildDefinition"]
    assert bd["externalParameters"]["scanId"] == str(scan_id)
    assert bd["externalParameters"]["projectId"] == str(project_id)
    # No secret material in the serialized predicate.
    blob = json.dumps(predicate)
    assert "dt-secret-should-not-appear" not in blob
    assert "DT_API_KEY" not in blob


def test_builder_id_and_version_read_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)
    att = tmp_path / "cosign" / "sbom.intoto.jsonl"
    att.parent.mkdir(parents=True, exist_ok=True)
    att.write_text("attestation")

    captured: dict[str, Any] = {}

    def _fake_attest(**kwargs: Any) -> AttestResult:
        captured.update(kwargs)
        return AttestResult(attested=True, mode="key", attestation_path=att)

    monkeypatch.setattr("tasks.scan_source.cosign_adapter.attest_blob", _fake_attest)
    _patch_persist(monkeypatch)

    monkeypatch.setenv("SLSA_BUILDER_ID", "https://ci.example.com/trustedoss")
    monkeypatch.setenv("TRUSTEDOSS_VERSION", "2.3.0-rc1")
    mod._attest_sbom(
        scan_uuid=uuid.uuid4(), project_id=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path
    )

    run = captured["predicate"]["runDetails"]
    assert run["builder"]["id"] == "https://ci.example.com/trustedoss"
    assert run["builder"]["version"]["trustedoss"] == "2.3.0-rc1"


# ---------------------------------------------------------------------------
# Column-width + sign→attest gating
# ---------------------------------------------------------------------------


def test_attestation_artifact_kinds_fit_column_width() -> None:
    assert len(mod._SBOM_ATTESTATION_KIND) <= 32
    assert len(mod._SBOM_ATTEST_CERT_KIND) <= 32


def test_sign_sbom_returns_true_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)
    sig = tmp_path / "cosign" / "sbom.cdx.json.sig"
    sig.parent.mkdir(parents=True, exist_ok=True)
    sig.write_text("signature")
    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.sign_blob",
        lambda **_k: SignResult(signed=True, mode="key", signature_path=sig),
    )
    _patch_persist(monkeypatch)
    assert mod._sign_sbom(scan_uuid=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path) is True


def test_sign_sbom_returns_false_on_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)
    monkeypatch.setattr(
        "tasks.scan_source.cosign_adapter.sign_blob",
        lambda **_k: SignResult(signed=False, skip_reason="cosign_not_installed"),
    )
    _patch_persist(monkeypatch)
    assert mod._sign_sbom(scan_uuid=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path) is False


def test_sign_sbom_returns_false_on_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom = _make_sbom(tmp_path)

    def _boom(**_k: Any) -> SignResult:
        raise RuntimeError("boom")

    monkeypatch.setattr("tasks.scan_source.cosign_adapter.sign_blob", _boom)
    _patch_persist(monkeypatch)
    assert mod._sign_sbom(scan_uuid=uuid.uuid4(), sbom_path=sbom, workspace=tmp_path) is False
