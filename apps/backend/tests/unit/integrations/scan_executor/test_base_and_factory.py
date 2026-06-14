"""ScanExecutor abstraction — request/result contract + factory selection.

Increment 1 is a behaviour-preserving refactor: these tests pin the dataclass
defaults and prove the factory degrades any non-``inprocess`` mode (none are
implemented yet) to the legacy in-process executor rather than erroring.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from integrations.scan_executor import (
    SbomGenRequest,
    SbomGenResult,
    get_executor,
)
from integrations.scan_executor.inprocess import InProcessExecutor
from integrations.scan_executor.local_docker import LocalDockerExecutor


def test_request_defaults_preserve_legacy_call() -> None:
    """A minimal request must default to the legacy in-process shape."""
    req = SbomGenRequest(
        scan_uuid=uuid.uuid4(),
        source_dir=Path("/src"),
        output_dir=Path("/out"),
    )
    assert req.detected_env == "inprocess"
    assert req.spec_version == "1.5"
    assert req.fetch_license is False
    assert req.verbose is False
    # None → cdxgen adapter applies its own default timeout (legacy passed none).
    assert req.timeout_seconds is None


def test_result_carries_provenance() -> None:
    res = SbomGenResult(
        sbom_path=Path("/out/cdxgen.cdx.json"),
        sbom={"bomFormat": "CycloneDX"},
        executor="inprocess",
        image=None,
        detected_env="inprocess",
    )
    assert res.executor == "inprocess"
    assert res.image is None


def test_request_and_result_are_frozen() -> None:
    req = SbomGenRequest(
        scan_uuid=uuid.uuid4(), source_dir=Path("/s"), output_dir=Path("/o")
    )
    with pytest.raises((AttributeError, TypeError)):
        req.spec_version = "1.6"  # type: ignore[misc]


def test_factory_default_is_inprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAN_EXECUTOR", raising=False)
    assert isinstance(get_executor(), InProcessExecutor)


def test_factory_explicit_inprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_EXECUTOR", "inprocess")
    assert isinstance(get_executor(), InProcessExecutor)


@pytest.mark.parametrize("mode", ["local_docker", "LOCAL_DOCKER"])
def test_factory_local_docker(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("SCAN_EXECUTOR", mode)
    assert isinstance(get_executor(), LocalDockerExecutor)


@pytest.mark.parametrize("mode", ["k8s_job", "bogus", "K8S_JOB"])
def test_factory_unimplemented_modes_fall_back_to_inprocess(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Until later increments wire them, non-inprocess modes degrade safely."""
    monkeypatch.setenv("SCAN_EXECUTOR", mode)
    assert isinstance(get_executor(), InProcessExecutor)


def test_factory_mode_argument_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_EXECUTOR", "inprocess")
    # Explicit arg wins over the env.
    assert isinstance(get_executor("local_docker"), LocalDockerExecutor)
